"""Subscriptions: a search plus a flat AND list of conditions, a naming scheme and options.

A check runs the search, applies the rules and queues what has no download entry yet (episode key and duration,
never the url; failed and cancelled entries count, removing the entry allows a new attempt). Checks run after every
film list import and on demand. The preview explains every hit
the way the design shows it: steps ("35 → 25 → 20"), the first rule that excluded a film and why, duplicates on
other channels, files that exist already.
"""

import asyncio
import contextlib
import datetime as dt
import json
import logging
import time
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from mtdl_web.downloads import DownloadError
from mtdl_web.downloads.manager import DownloadManager, Duplicate, is_livestream
from mtdl_web.downloads.queue import State as DownloadState
from mtdl_web.episodes import episode_key, same_duration, same_file
from mtdl_web.events import Event, EventBus, FilmlistUpdated, SubscriptionExpired
from mtdl_web.libraries import LibraryStore
from mtdl_web.library import EpisodeMatch, LibraryUnavailable, Shelf
from mtdl_web.metadata import EpisodeInfo, Tmdb
from mtdl_web.models import Film
from mtdl_web.naming import Naming, target_path
from mtdl_web.rules.engine import ConditionResult, RuleSet
from mtdl_web.rules.model import Condition, Field
from mtdl_web.sources import Source
from mtdl_web.special import SpecialVersions, excluded, special_version
from mtdl_web.state import AppSettings, StateStore
from mtdl_web.text import fold

log = logging.getLogger(__name__)

SEARCH_LIMIT = 2000  # hits per check; a series has hundreds at most

SubState = Literal["active", "paused"]


def condition_to_json(condition: Condition) -> dict[str, Any]:
    value: Any = condition.value
    if isinstance(value, dt.date):
        value = value.isoformat()
    elif isinstance(value, tuple):
        value = list(value)
    return {
        "field": str(condition.field),
        "operator": str(condition.operator),
        "value": value,
        "regex": condition.regex,
        "enabled": condition.enabled,
    }


def condition_from_json(data: dict[str, Any]) -> Condition:
    """Raises RuleError for anything invalid, with a code the UI can explain."""
    value = data.get("value")
    if data.get("field") == Field.DATE and isinstance(value, str):
        with contextlib.suppress(ValueError):  # otherwise Condition reports invalid_value
            value = dt.date.fromisoformat(value)
    return Condition(
        data.get("field"),  # type: ignore[arg-type]
        data.get("operator"),  # type: ignore[arg-type]
        value,  # type: ignore[arg-type]
        regex=bool(data.get("regex", False)),
        enabled=bool(data.get("enabled", True)),
    )


def naming_to_json(naming: Naming) -> dict[str, Any]:
    return {
        "scheme": naming.scheme,
        "show": naming.show,
        "folder": naming.folder,
        "season": naming.season,
        "template": naming.template,
        "target": naming.target,
        "tmdb_id": naming.tmdb_id,
        "library_id": naming.library_id,
    }


Expiry = Literal["pause", "delete"]


@dataclass(frozen=True, slots=True)
class Subscription:
    id: int
    name: str
    query: str
    include_description: bool
    source: str | None  # None: the source from the settings
    conditions: tuple[Condition, ...]
    naming: Naming
    quality: str | None  # None: from the settings
    subtitles: bool | None
    state: SubState
    created_at: int
    updated_at: int
    last_check: int | None = None
    last_error: str | None = None
    last_queued: int | None = None
    # The last day it searches ("Staffel 2 endet in drei Wochen"); then it pauses or goes.
    valid_until: dt.date | None = None
    on_expiry: Expiry = "pause"
    special_versions: SpecialVersions = "setting"  # audio description, sign language (special.py)


_COLUMNS = (
    "id, name, query, include_description, source, conditions, naming, quality, subtitles, state, created_at,"
    " updated_at, last_check, last_error, last_queued, valid_until, on_expiry, special_versions"
)


def _row(row: tuple[Any, ...]) -> Subscription:
    sid, name, query, desc, source, conditions, naming, quality, subtitles, state, created, updated, lc, le, lq = row[
        :15
    ]
    valid_until, on_expiry, special_versions = row[15:]
    return Subscription(
        id=sid,
        name=name,
        query=query,
        include_description=bool(desc),
        source=source,
        conditions=tuple(condition_from_json(c) for c in json.loads(conditions)),
        naming=Naming(**json.loads(naming)),
        quality=quality,
        subtitles=None if subtitles is None else bool(subtitles),
        state=state,
        created_at=created,
        updated_at=updated,
        last_check=lc,
        last_error=le,
        last_queued=lq,
        valid_until=dt.date.fromisoformat(valid_until) if valid_until else None,
        on_expiry=on_expiry,
        special_versions=special_versions,
    )


@dataclass(frozen=True, slots=True)
class SubscriptionDraft:
    name: str
    query: str
    conditions: tuple[Condition, ...]
    naming: Naming = field(default_factory=Naming)
    include_description: bool = False
    source: str | None = None
    quality: str | None = None
    subtitles: bool | None = None
    state: SubState = "active"
    valid_until: dt.date | None = None
    on_expiry: Expiry = "pause"
    special_versions: SpecialVersions = "setting"


def expired(subscription: Subscription, today: dt.date) -> bool:
    """Past its last day: the day itself still counts."""
    return subscription.valid_until is not None and today > subscription.valid_until


class SubscriptionStore:
    def __init__(self, state: StateStore) -> None:
        self._db = state.db

    def _values(self, draft: SubscriptionDraft) -> tuple[Any, ...]:
        return (
            draft.name,
            draft.query,
            int(draft.include_description),
            draft.source,
            json.dumps([condition_to_json(c) for c in draft.conditions], ensure_ascii=False),
            json.dumps(naming_to_json(draft.naming), ensure_ascii=False),
            draft.quality,
            None if draft.subtitles is None else int(draft.subtitles),
            draft.state,
            draft.valid_until.isoformat() if draft.valid_until else None,
            draft.on_expiry,
            draft.special_versions,
        )

    def add(self, draft: SubscriptionDraft) -> Subscription:
        now = int(time.time())
        _, subscription_id = self._db.run(
            "INSERT INTO subscription (name, query, include_description, source, conditions, naming, quality,"
            " subtitles, state, valid_until, on_expiry, special_versions, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (*self._values(draft), now, now),
        )
        result = self.get(int(subscription_id or 0))
        assert result is not None
        return result

    def update(self, subscription_id: int, draft: SubscriptionDraft) -> Subscription | None:
        changed, _ = self._db.run(
            "UPDATE subscription SET name = ?, query = ?, include_description = ?, source = ?, conditions = ?,"
            " naming = ?, quality = ?, subtitles = ?, state = ?, valid_until = ?, on_expiry = ?, special_versions = ?,"
            " updated_at = ?"
            " WHERE id = ?",
            (*self._values(draft), int(time.time()), subscription_id),
        )
        return self.get(subscription_id) if changed else None

    def get(self, subscription_id: int) -> Subscription | None:
        row = self._db.one(f"SELECT {_COLUMNS} FROM subscription WHERE id = ?", (subscription_id,))
        return None if row is None else _row(row)

    def entries(self, state: SubState | None = None) -> list[Subscription]:
        sql = f"SELECT {_COLUMNS} FROM subscription"
        params: tuple[Any, ...] = ()
        if state:
            sql += " WHERE state = ?"
            params = (state,)
        return [_row(r) for r in self._db.all(sql + " ORDER BY name COLLATE NOCASE", params)]

    def delete(self, subscription_id: int) -> bool:
        with self._db.transaction() as con:
            con.execute("DELETE FROM subscription_skip WHERE subscription_id = ?", (subscription_id,))
            changed = con.execute("DELETE FROM subscription WHERE id = ?", (subscription_id,)).rowcount
        return changed > 0

    def skip(self, subscription_id: int, keys: Iterable[str]) -> None:
        now = int(time.time())
        with self._db.transaction() as con:
            con.executemany(
                "INSERT OR IGNORE INTO subscription_skip (subscription_id, episode_key, created_at) VALUES (?, ?, ?)",
                [(subscription_id, key, now) for key in keys],
            )

    def skipped(self, subscription_id: int) -> frozenset[str]:
        rows = self._db.all("SELECT episode_key FROM subscription_skip WHERE subscription_id = ?", (subscription_id,))
        return frozenset(r[0] for r in rows)

    def unskip(self, subscription_id: int) -> int:
        changed, _ = self._db.run("DELETE FROM subscription_skip WHERE subscription_id = ?", (subscription_id,))
        return changed

    def pause(self, subscription_id: int) -> None:
        self._db.run(
            "UPDATE subscription SET state = 'paused', updated_at = ? WHERE id = ?", (int(time.time()), subscription_id)
        )

    def checked(self, subscription_id: int, *, error: str | None, queued: int) -> None:
        self._db.run(
            "UPDATE subscription SET last_check = ?, last_error = ?, last_queued = ? WHERE id = ?",
            (int(time.time()), error, queued, subscription_id),
        )

    def stats(self) -> dict[int, dict[str, Any]]:
        """Per subscription: downloads per state, skipped episodes, and the newest done download."""
        out: dict[int, dict[str, Any]] = {}
        for sid, state, count in self._db.all(
            "SELECT subscription_id, state, count(*) FROM download WHERE subscription_id IS NOT NULL"
            " GROUP BY subscription_id, state"
        ):
            out.setdefault(sid, {})[state] = count
        skips = "SELECT subscription_id, count(*) FROM subscription_skip GROUP BY subscription_id"
        for sid, count in self._db.all(skips):
            out.setdefault(sid, {})["skipped"] = count
        for sid, title, finished in self._db.all(
            "SELECT subscription_id, json_extract(film, '$.title'), max(finished_at) FROM download"
            " WHERE subscription_id IS NOT NULL AND state = 'done' GROUP BY subscription_id"
        ):
            out.setdefault(sid, {})["last_title"] = title
            out[sid]["last_finished"] = finished
        return out


Status = Literal[
    "load", "exists", "excluded", "duplicate", "unavailable", "failed", "cancelled", "skipped", "unmatched"
]
# A failed or cancelled download counts as handled: a subscription does not queue it again on every check (a gone
# episode would fail each hour). Retrying it, or removing the entry, is the user's decision.
HANDLED: tuple[DownloadState, ...] = ("running", "queued", "done", "failed", "cancelled")


@dataclass(frozen=True, slots=True)
class PreviewItem:
    film: Film
    status: Status
    results: tuple[ConditionResult, ...]
    excluded_by: int | None  # index of the first condition that excluded the film
    path: PurePosixPath | None
    duplicate_of: int | None = None  # film id of the hit this one repeats (other channel)
    download_id: int | None = None  # the download that has it already
    # Library target: the TMDB episode and why (library.EpisodeMatch), or why none ("unmatched").
    episode: EpisodeInfo | None = None
    reason: str | None = None
    in_library: bool = False  # "exists" because the library has the episode, not because mtdl-web loaded it
    library_id: int | None = None  # the library ``path`` (absolute) lies in
    special: str | None = None  # the marker of a special version ("Audiodeskription"), excluded or not


@dataclass(frozen=True, slots=True)
class PreviewStep:
    index: int
    enabled: bool
    before: int
    excluded: int
    remaining: int
    # "special_versions": the step after the conditions that leaves out audio description and the like (special.py)
    kind: Literal["condition", "special_versions"] = "condition"


@dataclass(frozen=True, slots=True)
class Preview:
    total: int  # hits of the search
    steps: tuple[PreviewStep, ...]
    items: tuple[PreviewItem, ...]

    @property
    def to_load(self) -> list[PreviewItem]:
        return [i for i in self.items if i.status == "load"]


class SubscriptionService:
    def __init__(
        self,
        store: SubscriptionStore,
        downloads: DownloadManager,
        sources: Any,  # callable(name or None) -> Source
        settings: Any,  # callable() -> AppSettings
        download_dir: Path,
        bus: EventBus,
        timezone: dt.tzinfo,
        tmdb: Tmdb,
        libraries: LibraryStore,
    ) -> None:
        self.store = store
        self._downloads = downloads
        self._sources = sources
        self._settings = settings
        self._dir = download_dir
        self._tz = timezone
        self._tmdb = tmdb
        self._libraries = libraries
        self._bus = bus
        self._lock = asyncio.Lock()
        self._pending: asyncio.Task[dict[int, int]] | None = None
        bus.subscribe(self._on_event)

    def _on_event(self, event: Event) -> None:
        idle = self._pending is None or self._pending.done()
        if isinstance(event, FilmlistUpdated) and (event.added or event.changed) and idle:
            self._pending = asyncio.create_task(self.check_all(), name="subscription-check")

    async def preview(self, draft: SubscriptionDraft, skipped: frozenset[str] = frozenset()) -> Preview:
        """``skipped``: episode keys the subscription leaves alone (saved with "only future episodes")."""
        source: Source = self._sources(draft.source)
        found = await source.search(draft.query, include_description=draft.include_description, limit=SEARCH_LIMIT)
        films = sorted(found.films, key=lambda f: (f.timestamp or 0, f.id))  # oldest first: queued in order
        rules = RuleSet(draft.conditions, timezone=self._tz)
        settings: AppSettings = self._settings()
        evaluations = [rules.evaluate(f) for f in films]

        steps = []
        remaining = len(films)
        excluded_by: dict[int, int] = {}
        for index, condition in enumerate(draft.conditions):
            before = remaining
            if condition.enabled:
                for evaluation in evaluations:
                    film_id = evaluation.film.id
                    if film_id not in excluded_by and not evaluation.results[index].passed:
                        excluded_by[film_id] = index
                remaining = len(films) - len(excluded_by)
            steps.append(PreviewStep(index, condition.enabled, before, before - remaining, remaining))
        # Special versions last, after the conditions; the step shows even when it leaves nothing out.
        specials = {e.film.id: marker for e in evaluations if (marker := special_version(e.film))}
        if excluded(draft.special_versions, settings.exclude_special_versions):
            index, before = len(draft.conditions), remaining
            for film_id in specials:
                excluded_by.setdefault(film_id, index)
            remaining = len(films) - len(excluded_by)
            steps.append(PreviewStep(index, True, before, before - remaining, remaining, kind="special_versions"))

        representative = _representatives([e.film for e in evaluations if e.film.id not in excluded_by], self._tz)
        shelf = await self._shelf(draft.naming) if draft.naming.target == "library" else None
        out: dict[int, PreviewItem] = {}
        placed: list[tuple[Film, tuple[ConditionResult, ...], PurePosixPath, EpisodeMatch | None]] = []
        for evaluation in evaluations:
            film, results = evaluation.film, evaluation.results
            if film.id in excluded_by:
                out[film.id] = PreviewItem(film, "excluded", results, excluded_by[film.id], None)
                continue
            twin = representative[film.id]
            if twin.id != film.id:
                path = (
                    None
                    if shelf
                    else target_path(film, draft.naming, normalize=settings.normalize_filenames, timezone=self._tz)
                )
                out[film.id] = PreviewItem(film, "duplicate", results, None, path, duplicate_of=twin.id)
                continue
            if is_livestream(film):
                out[film.id] = PreviewItem(film, "unavailable", results, None, None)
                continue
            if shelf is None:
                path = target_path(film, draft.naming, normalize=settings.normalize_filenames, timezone=self._tz)
                placed.append((film, results, path, None))
                continue
            match = shelf.match(film)
            if match.episode is None:
                out[film.id] = PreviewItem(film, "unmatched", results, None, None, reason=match.reason)
                continue
            placed.append((film, results, shelf.path(film, match.episode), match))

        # Two entries for one library episode (a clip named like it, a repeat on another day): the longest loads.
        owner: dict[tuple[int, int], Film] = {}
        for film, _, _, hit in sorted(placed, key=lambda p: -(p[0].duration or 0)):
            if hit is not None and hit.episode is not None:
                owner.setdefault((hit.episode.season, hit.episode.number), film)

        for film, results, path, hit in placed:
            episode = hit.episode if hit else None
            reason = hit.reason if hit else None
            if episode is not None and (first := owner[(episode.season, episode.number)]).id != film.id:
                out[film.id] = PreviewItem(
                    film, "duplicate", results, None, path, duplicate_of=first.id, episode=episode, reason=reason
                )
                continue
            existing = self._downloads.find_existing(film, HANDLED)
            in_library = shelf is not None and episode is not None and shelf.has(episode, path)
            if existing is not None and existing.state in ("failed", "cancelled"):
                status: Status = "failed" if existing.state == "failed" else "cancelled"
            elif existing is not None or in_library or (shelf is None and (self._dir / path).exists()):
                status = "exists"
            elif episode_key(film) in skipped:
                status = "skipped"
            else:
                status = "load"
            out[film.id] = PreviewItem(
                film,
                status,
                results,
                None,
                path,
                download_id=existing.id if existing else None,
                episode=episode,
                reason=reason,
                in_library=in_library,
                library_id=shelf.library.id if shelf is not None else None,
            )
        items = tuple(replace(out[e.film.id], special=specials.get(e.film.id)) for e in evaluations)
        return Preview(len(films), tuple(steps), items)

    async def _shelf(self, naming: Naming) -> Shelf:
        """The series library of the subscription: the one named, or the only series library (subscriptions of
        0.1.0.dev16 to dev19 name none)."""
        if naming.library_id is not None:
            library = self._libraries.get(naming.library_id)
            if library is None:
                raise LibraryUnavailable(f"library {naming.library_id} no longer exists")
        elif (library := self._libraries.only("series")) is None:
            raise LibraryUnavailable("no series library named, and not exactly one exists")
        if library.kind != "series":
            raise LibraryUnavailable(f"library {library.name} holds movies, not series")
        series = await self._tmdb.series(naming.tmdb_id) if naming.tmdb_id is not None else None
        return Shelf(series, library, timezone=self._tz)

    async def expire(self, subscription: Subscription) -> bool:
        """Past its last day: pause it (or delete it, files and history stay) and tell the bus. False if it is not
        due yet."""
        if subscription.state != "active" or not expired(subscription, dt.datetime.now(self._tz).date()):
            return False
        assert subscription.valid_until is not None
        if subscription.on_expiry == "delete":
            self.store.delete(subscription.id)
        else:
            self.store.pause(subscription.id)
        action = "deleted" if subscription.on_expiry == "delete" else "paused"
        log.info(
            "subscription %d (%s) %s: valid until %s",
            subscription.id,
            subscription.name,
            action,
            subscription.valid_until,
        )
        await self._bus.publish(
            SubscriptionExpired(
                subscription_id=subscription.id,
                subscription_name=subscription.name,
                valid_until=subscription.valid_until.isoformat(),
                action=action,
            )
        )
        return True

    async def requeue(self, subscription: Subscription) -> tuple[int, int]:
        """Load the episodes again that failed or were cancelled: the history forgets them, a check queues them with
        today's naming and quality (a retry would keep the old target). Returns (forgotten, queued)."""
        forgotten = self._downloads.queue.forget_given_up(subscription.id)
        return forgotten, await self.check(subscription)

    async def check(self, subscription: Subscription) -> int:
        """Queue what the subscription finds and nobody has yet; returns the number queued. One past its last day
        pauses itself (or goes) instead."""
        if await self.expire(subscription):
            return 0
        draft = draft_of(subscription)
        queued = 0
        error = None
        try:
            preview = await self.preview(draft, self.store.skipped(subscription.id))
            for item in preview.to_load:
                assert item.path is not None
                path = item.path
                if item.library_id is not None:  # stored relative to the library, so it may move
                    library = self._libraries.get(item.library_id)
                    assert library is not None
                    path = path.relative_to(library.path)
                try:
                    await self._downloads.enqueue(
                        item.film,
                        path,
                        quality=subscription.quality,
                        subtitles=subscription.subtitles,
                        subscription_id=subscription.id,
                        library_id=item.library_id,
                    )
                    queued += 1
                except (Duplicate, DownloadError):
                    continue
        except Exception as exc:  # one broken subscription must not stop the others
            error = f"{type(exc).__name__}: {exc}"
            log.warning("subscription %d (%s) failed: %s", subscription.id, subscription.name, error)
        self.store.checked(subscription.id, error=error, queued=queued)
        if queued:
            log.info("subscription %d (%s): %d queued", subscription.id, subscription.name, queued)
        return queued

    async def current_keys(self, draft: SubscriptionDraft, skipped: frozenset[str] = frozenset()) -> list[str]:
        """What ``draft`` would load now, as episode keys: "only future episodes" saves them as skipped. Asked before
        the subscription is stored, so a source that fails leaves nothing half saved."""
        preview = await self.preview(draft, skipped)
        return [episode_key(item.film) for item in preview.to_load]

    async def check_all(self) -> dict[int, int]:
        async with self._lock:
            return {s.id: await self.check(s) for s in self.store.entries("active")}


def _same_broadcast(a: Film, b: Film, timezone: dt.tzinfo) -> bool:
    same_key = episode_key(a, timezone) == episode_key(b, timezone) and same_duration(a.duration, b.duration)
    return same_key or same_file(a, b, timezone)


# Versions the film list sometimes points at the plain file (13.10.2026: "Maus-Spezial: Frankreich
# (Audiodeskription)") never name the file; special.py knows them.


def _representatives(films: list[Film], timezone: dt.tzinfo) -> dict[int, Film]:
    """For every film the one that stands for its broadcast (itself, or a twin on another channel): the show's home
    channel, the one with the most hits on the topic (ZDF for "Die Anstalt", not 3sat), then a plain version over
    audio description or sign language, then the longer title, which names the episode more often ("Die Anstalt –
    Jetzt erst rechts! …")."""
    home = Counter((fold(f.topic), f.channel) for f in films)
    groups: list[list[Film]] = []
    for film in films:
        group = next((g for g in groups if any(_same_broadcast(m, film, timezone) for m in g)), None)
        if group is None:
            groups.append([film])
        else:
            group.append(film)
    chosen: dict[int, Film] = {}
    for group in groups:
        best = max(
            group,
            key=lambda f: (home[(fold(f.topic), f.channel)], special_version(f) is None, len(f.title), f.id),
        )
        chosen |= {member.id: best for member in group}
    return chosen


def draft_of(subscription: Subscription) -> SubscriptionDraft:
    return SubscriptionDraft(
        name=subscription.name,
        query=subscription.query,
        conditions=subscription.conditions,
        naming=subscription.naming,
        include_description=subscription.include_description,
        source=subscription.source,
        quality=subscription.quality,
        subtitles=subscription.subtitles,
        state=subscription.state,
        valid_until=subscription.valid_until,
        on_expiry=subscription.on_expiry,
        special_versions=subscription.special_versions,
    )
