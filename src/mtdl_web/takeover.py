"""Takeover from MediathekView (the desktop app): download history and subscriptions.

Its history (``~/.mediathek3/history.txt``; downloads of subscriptions also in ``downloadAbos.txt``) has one line per
download: ``12.10.2026 |#| <topic, cut to 25> |#| <title, cut to ~40> |###|  <url>``, the date is the day of the
download. The url finds most films in the film list; otherwise the beginnings of topic and title do, if they point
at a single broadcast (the identity is never the url alone). What the film list no longer has cannot be loaded
again anyway and is only counted. Taken-over downloads count as done, so subscriptions skip them.

Subscriptions come from ``mediathek.xml`` (``<Abonnement>``) and arrive paused; their conditions are ordinary
visible ones. Values with MediathekView's regex prefix ``#:`` become regex conditions.
"""

import dataclasses
import datetime as dt
import re
import xml.etree.ElementTree as ET
import zoneinfo
from typing import Literal

from mtdl_web.downloads.manager import DownloadManager
from mtdl_web.episodes import episode_key, same_duration
from mtdl_web.filmlist.store import FilmStore, url_folder
from mtdl_web.models import Film
from mtdl_web.naming import Naming
from mtdl_web.rules.engine import RuleSet
from mtdl_web.rules.model import Condition, Field, Operator, RuleError
from mtdl_web.subscriptions import HANDLED, SubscriptionDraft, SubscriptionStore
from mtdl_web.text import fold

BERLIN = zoneinfo.ZoneInfo("Europe/Berlin")
_LINE = re.compile(r"^\s*(\d{2}\.\d{2}\.\d{4})?\s*\|#\|(.*?)\|#\|(.*?)\|###\|\s*(\S+)\s*$")
TITLE_WIDTH = 38  # titles this long or longer were probably cut, the last word may be partial

How = Literal["url", "folder", "title", "ambiguous", "missing"]


@dataclasses.dataclass(frozen=True, slots=True)
class HistoryLine:
    number: int  # line in the file, from 1
    day: dt.date | None  # day of the download, not of the broadcast
    topic: str  # the beginning of the topic
    title: str  # the beginning of the title
    url: str


def parse_history(text: str) -> list[HistoryLine]:
    lines = []
    for number, raw in enumerate(text.splitlines(), start=1):
        m = _LINE.match(raw)
        if not m:
            continue
        day = None
        if m[1]:
            try:
                day = dt.datetime.strptime(m[1], "%d.%m.%Y").date()
            except ValueError:
                day = None
        lines.append(HistoryLine(number, day, m[2].strip(), m[3].strip(), m[4]))
    return lines


@dataclasses.dataclass(frozen=True, slots=True)
class Found:
    lines: tuple[HistoryLine, ...]  # every history line of this episode (downloaded more than once)
    films: tuple[Film, ...]  # the episode; several entries when it was repeated on other days
    how: How

    @property
    def film(self) -> Film | None:
        return self.films[0] if self.films else None


def _one_episode(candidates: list[Film]) -> bool:
    """One episode: a single entry, or repeats with the same topic, title and duration (other day or channel)."""
    first = candidates[0]
    return all(
        fold(c.topic) == fold(first.topic)
        and fold(c.title) == fold(first.title)
        and same_duration(c.duration, first.duration)
        for c in candidates[1:]
    )


def _by_title(line: HistoryLine, films: FilmStore) -> tuple[tuple[Film, ...], How]:
    words = re.findall(r"\w+", line.title)
    if len(line.title) >= TITLE_WIDTH and len(words) > 1:
        words = words[:-1]  # cut in the middle of a word
    if not words:
        return (), "missing"
    result = films.search(" ".join(words), scope="title", limit=200)
    topic, title = fold(line.topic), fold(line.title)
    cut = (
        len(line.title) >= TITLE_WIDTH
    )  # a shorter title is complete: "… (S04/E02)" is not "… (S04/E02) - Audiodeskription"
    candidates = [
        f
        for f in result.films
        if fold(f.topic).startswith(topic) and (fold(f.title).startswith(title) if cut else fold(f.title) == title)
    ]
    if not candidates:
        return (), "missing"
    if _one_episode(candidates):
        return tuple(candidates), "title"
    return (), "ambiguous"


def find_films(lines: list[HistoryLine], films: FilmStore) -> list[Found]:
    """One entry per episode (or per unmatched line), in the order of the history: by the exact url, else by the
    url's folder (another quality of the same file), else by the beginnings of topic and title."""
    by_url = films.by_urls(line.url for line in lines)
    open_lines = [line for line in lines if line.url not in by_url]
    by_folder = films.by_url_folders(url_folder(line.url) for line in open_lines) if open_lines else {}
    grouped: dict[str, list[HistoryLine]] = {}
    matched: dict[str, tuple[tuple[Film, ...], How]] = {}
    for line in lines:
        found: tuple[Film, ...]
        how: How
        if (film := by_url.get(line.url)) is not None:
            found, how = (film,), "url"
        elif (same_folder := by_folder.get(url_folder(line.url))) and _one_episode(same_folder):
            found, how = tuple(same_folder), "folder"
        else:
            found, how = _by_title(line, films)
        key = f"film:{found[0].id}" if found else f"line:{line.number}"
        grouped.setdefault(key, []).append(line)
        matched.setdefault(key, (found, how))
    return [Found(tuple(group), *matched[key]) for key, group in grouped.items()]


# -- subscriptions ---------------------------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class OldSubscription:
    name: str
    active: bool
    channel: str
    topic: str
    title: str
    topic_or_title: str
    anywhere: str
    minutes: int
    at_least: bool
    folder: str


def parse_subscriptions(xml_text: str) -> list[OldSubscription]:
    """``<Abonnement>`` entries of mediathek.xml; raises ``ValueError`` if the text is not XML."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"not readable as XML: {exc}") from exc
    found = []
    for abo in root.iter("Abonnement"):

        def text(tag: str, abo: ET.Element = abo) -> str:
            return (abo.findtext(tag) or "").strip()

        minutes = text("Mindestdauer")
        found.append(
            OldSubscription(
                name=text("Name"),
                active=text("aktiv").lower() != "false",
                channel=text("Sender"),
                topic=text("Thema"),
                title=text("Titel"),
                topic_or_title=text("Thema-Titel"),
                anywhere=text("Irgendwo"),
                minutes=int(minutes) if minutes.isdigit() else 0,
                at_least=text("min_max").lower() != "false",
                folder=text("Zielpfad"),
            )
        )
    return found


def _text_condition(field: Field, value: str, plain: Operator) -> Condition:
    if value.startswith("#:"):
        return Condition(field, Operator.IS, value[2:], regex=True)
    return Condition(field, plain, value)


@dataclasses.dataclass(frozen=True, slots=True)
class Converted:
    old: OldSubscription
    draft: SubscriptionDraft | None
    notes: tuple[str, ...]  # codes the UI explains: query_from, regex, description, invalid
    error: str | None = None


def convert(old: OldSubscription) -> Converted:
    notes: list[str] = []
    conditions: list[Condition] = []
    try:
        if old.channel:
            conditions.append(Condition(Field.CHANNEL, Operator.ANY_OF, tuple(old.channel.split(","))))
        if old.topic:
            conditions.append(_text_condition(Field.TOPIC, old.topic, Operator.IS))
        if old.title:
            conditions.append(_text_condition(Field.TITLE, old.title, Operator.CONTAINS))
        if old.minutes > 0:
            operator = Operator.AT_LEAST if old.at_least else Operator.AT_MOST
            conditions.append(Condition(Field.DURATION, operator, old.minutes))
    except RuleError as exc:
        return Converted(old, None, ("invalid",), f"{exc.code}: {exc.params}")
    if any(c.regex for c in conditions):
        notes.append("regex")
    # The search finds candidates, the conditions decide. "Topic or title" and "anywhere" have no AND form: they
    # become the search words (anywhere also searches descriptions).
    query = next((v for v in (old.topic_or_title, old.anywhere) if v), "")
    if query:
        notes.append("query_from")
    elif old.topic and not old.topic.startswith("#:"):
        query = old.topic
    elif old.title and not old.title.startswith("#:"):
        query = old.title
    else:
        query = old.name.replace("_", " ")
    if old.anywhere:
        notes.append("description")
    name = old.topic if old.topic and not old.topic.startswith("#:") else old.name.replace("_", " ").strip()
    draft = SubscriptionDraft(
        name=name or query,
        query=query,
        conditions=tuple(conditions),
        naming=Naming("season_episode", folder=old.folder or None),
        include_description=bool(old.anywhere),
        state="paused",
    )
    return Converted(old, draft, tuple(notes))


# -- the whole takeover ----------------------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class Item:
    found: Found
    status: Literal["new", "known", "missing", "ambiguous"]
    subscription: int | None  # index into the converted subscriptions, for downloads of a subscription


@dataclasses.dataclass(frozen=True, slots=True)
class Report:
    lines: int
    items: tuple[Item, ...]
    subscriptions: tuple[Converted, ...]
    existing_names: frozenset[str]  # subscriptions with these names exist already: not taken over twice
    applied: bool = False
    imported_downloads: int = 0
    imported_subscriptions: int = 0


def plan(
    history: str,
    abo_history: str,
    settings_xml: str,
    *,
    films: FilmStore,
    downloads: DownloadManager,
    subscriptions: SubscriptionStore,
) -> Report:
    own, from_abos = parse_history(history), parse_history(abo_history)
    seen = {line.url for line in own}
    lines = own + [line for line in from_abos if line.url not in seen]  # abo downloads are usually in both files
    abo_urls = {line.url for line in from_abos}
    converted = tuple(convert(old) for old in parse_subscriptions(settings_xml)) if settings_xml.strip() else ()
    rules = [RuleSet(c.draft.conditions) if c.draft else None for c in converted]
    items = []
    for found in find_films(lines, films):
        if found.film is None:
            items.append(Item(found, "missing" if found.how == "missing" else "ambiguous", None))
            continue
        known = all(downloads.find_existing(f, HANDLED) for f in found.films)
        status: Literal["new", "known"] = "known" if known else "new"
        owner = None
        if any(line.url in abo_urls for line in found.lines):
            owner = next((i for i, r in enumerate(rules) if r is not None and r.evaluate(found.film).passed), None)
        items.append(Item(found, status, owner))
    names = frozenset(s.name for s in subscriptions.entries())
    return Report(len(lines), tuple(items), converted, names)


def apply(
    report: Report, *, downloads: DownloadManager, subscriptions: SubscriptionStore, timezone: dt.tzinfo = BERLIN
) -> Report:
    existing = {s.name: s.id for s in subscriptions.entries()}
    owner: dict[int, int] = {}  # index in the report → subscription id (taken over now, or with that name already)
    created = 0
    for index, converted in enumerate(report.subscriptions):
        if converted.draft is None:
            continue
        if converted.draft.name in existing:
            owner[index] = existing[converted.draft.name]
        else:
            owner[index] = subscriptions.add(converted.draft).id
            created += 1
    imported = 0
    for item in report.items:
        if item.status != "new":
            continue
        day = max((line.day for line in item.found.lines if line.day), default=None)
        moment = dt.datetime.combine(day, dt.time(12), timezone) if day else dt.datetime.now(dt.UTC)
        finished = int(moment.timestamp())
        sub_id = owner.get(item.subscription) if item.subscription is not None else None
        for film in item.found.films:  # repeats of the episode count as loaded, too
            if downloads.find_existing(film, HANDLED) is None:
                downloads.queue.add_done(
                    film, episode_key=episode_key(film), finished_at=finished, subscription_id=sub_id
                )
                imported += 1
    return dataclasses.replace(report, applied=True, imported_downloads=imported, imported_subscriptions=created)
