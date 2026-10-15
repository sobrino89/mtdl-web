"""Download queue and history in the state database: survives restarts; done entries are the history that keeps
subscriptions from loading an episode twice (the episode key, or the same file on the same day)."""

import json
import time
from collections.abc import Collection, Iterable
from dataclasses import asdict, dataclass
from typing import Any, Literal

from mtdl_web.models import Film
from mtdl_web.state import StateStore

State = Literal["queued", "running", "done", "failed", "cancelled"]
Order = Literal["newest", "queue"]
ACTIVE: tuple[State, ...] = ("running", "queued", "done")
_COLUMNS = (
    "id, film, subscription_id, episode_key, quality, subtitles, path, state, bytes_done, bytes_total, error,"
    " attempts, not_before, created_at, started_at, finished_at, position, error_code, library_id"
)
_NEXT_POSITION = "(SELECT coalesce(max(position), 0) + 1 FROM download)"


@dataclass(frozen=True, slots=True)
class Download:
    id: int
    film: Film
    subscription_id: int | None
    episode_key: str
    quality: str
    subtitles: bool
    path: str  # relative to the library (library_id), else to the download folder
    state: State
    bytes_done: int
    bytes_total: int | None
    error: str | None
    attempts: int
    not_before: int | None
    created_at: int
    started_at: int | None
    finished_at: int | None
    position: int  # order in the queue: lower starts first
    error_code: str | None  # kind of the last failure (manager.error_code); the UI explains it
    library_id: int | None = None  # the library the file goes to (libraries.py); None: the download folder


def film_to_json(film: Film) -> str:
    data = asdict(film)
    data["geo"] = list(film.geo)
    return json.dumps(data, ensure_ascii=False)


def film_from_json(text: str) -> Film:
    data = json.loads(text)
    data["geo"] = tuple(data.get("geo") or ())
    return Film(**data)


def _download(row: tuple[Any, ...]) -> Download:
    did, film, sub, key, quality, subtitles, path, state, done, total, error, attempts, nb, created, started, fin = row[
        :16
    ]
    position, error_code, library_id = row[16:]
    return Download(
        id=did,
        film=film_from_json(film),
        subscription_id=sub,
        episode_key=key,
        quality=quality,
        subtitles=bool(subtitles),
        path=path,
        state=state,
        bytes_done=done,
        bytes_total=total,
        error=error,
        attempts=attempts,
        not_before=nb,
        created_at=created,
        started_at=started,
        finished_at=fin,
        position=position if position is not None else did,
        error_code=error_code,
        library_id=library_id,
    )


class DownloadQueue:
    def __init__(self, state: StateStore, clock: Any = time.time) -> None:
        self._db = state.db
        self._clock = clock

    def _now(self) -> int:
        return int(self._clock())

    def add(
        self,
        film: Film,
        *,
        path: str,
        quality: str,
        subtitles: bool,
        episode_key: str,
        subscription_id: int | None = None,
        library_id: int | None = None,
    ) -> Download:
        _, download_id = self._db.run(
            "INSERT INTO download (film_id, film, subscription_id, episode_key, quality, subtitles, path, state,"
            f" created_at, position, library_id) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, {_NEXT_POSITION}, ?)",
            (
                film.id,
                film_to_json(film),
                subscription_id,
                episode_key,
                quality,
                int(subtitles),
                path,
                self._now(),
                library_id,
            ),
        )
        result = self.get(int(download_id or 0))
        assert result is not None
        return result

    def add_done(
        self, film: Film, *, episode_key: str, finished_at: int, subscription_id: int | None = None
    ) -> Download:
        """A download made elsewhere (takeover from MediathekView): counts as done, has no file of ours."""
        _, download_id = self._db.run(
            "INSERT INTO download (film_id, film, subscription_id, episode_key, quality, subtitles, path, state,"
            f" created_at, finished_at, position) VALUES (?, ?, ?, ?, '', 0, '', 'done', ?, ?, {_NEXT_POSITION})",
            (film.id, film_to_json(film), subscription_id, episode_key, finished_at, finished_at),
        )
        result = self.get(int(download_id or 0))
        assert result is not None
        return result

    def get(self, download_id: int) -> Download | None:
        row = self._db.one(f"SELECT {_COLUMNS} FROM download WHERE id = ?", (download_id,))
        return None if row is None else _download(row)

    def entries(
        self,
        states: tuple[State, ...] | None = None,
        *,
        limit: int = 200,
        offset: int = 0,
        order: Order = "newest",
        subscription_id: int | None = None,
    ) -> list[Download]:
        """Newest first, or in queue order (what starts next first); optionally only one subscription's."""
        sql = f"SELECT {_COLUMNS} FROM download"
        where: list[str] = []
        params: tuple[Any, ...] = ()
        if states:
            where.append(f"state IN ({', '.join('?' * len(states))})")
            params += states
        if subscription_id is not None:
            where.append("subscription_id = ?")
            params += (subscription_id,)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY position, id" if order == "queue" else " ORDER BY id DESC"
        sql += " LIMIT ? OFFSET ?"
        return [_download(r) for r in self._db.all(sql, (*params, limit, offset))]

    def counts(self) -> dict[str, int]:
        return {state: count for state, count in self._db.all("SELECT state, count(*) FROM download GROUP BY state")}

    def by_episode(self, key: str, states: tuple[State, ...] = ACTIVE) -> list[Download]:
        """Downloads of the same broadcast in the given states; ordered like ``states``, then newest first."""
        rows = self._db.all(
            f"SELECT {_COLUMNS} FROM download WHERE episode_key = ? AND state IN ({', '.join('?' * len(states))})"
            " ORDER BY id DESC",
            (key, *states),
        )
        return sorted((_download(r) for r in rows), key=lambda d: states.index(d.state))

    def by_video(self, urls: Iterable[str], states: tuple[State, ...] = ACTIVE) -> list[Download]:
        """Downloads of a film with one of ``urls`` (any quality) in the given states; ordered like ``by_episode``."""
        wanted = sorted(set(urls))
        if not wanted:
            return []
        marks = ", ".join("?" * len(wanted))
        rows = self._db.all(
            f"SELECT {_COLUMNS} FROM download WHERE state IN ({', '.join('?' * len(states))}) AND ("
            f"json_extract(film, '$.url') IN ({marks}) OR json_extract(film, '$.url_hd') IN ({marks})"
            f" OR json_extract(film, '$.url_low') IN ({marks})) ORDER BY id DESC",
            (*states, *wanted, *wanted, *wanted),
        )
        return sorted((_download(r) for r in rows), key=lambda d: states.index(d.state))

    def claim_next(self, *, skip_download_dir: bool = False, skip_libraries: Collection[int] = ()) -> Download | None:
        """The first queued download in queue order that may start now, marked running. Downloads into a folder
        that cannot be written (the download folder, or one of ``skip_libraries``) wait."""
        now = self._now()
        sql = "SELECT id FROM download WHERE state = 'queued' AND (not_before IS NULL OR not_before <= ?)"
        params: list[Any] = [now]
        if skip_download_dir:
            sql += " AND library_id IS NOT NULL"
        if skip_libraries:
            sql += f" AND (library_id IS NULL OR library_id NOT IN ({', '.join('?' * len(skip_libraries))}))"
            params += list(skip_libraries)
        with self._db.transaction() as con:
            row = con.execute(sql + " ORDER BY position, id LIMIT 1", params).fetchone()
            if row is None:
                return None
            con.execute(
                "UPDATE download SET state = 'running', started_at = ?, attempts = attempts + 1, error = NULL"
                " WHERE id = ?",
                (now, row[0]),
            )
        return self.get(row[0])

    def next_wakeup(self) -> int | None:
        row = self._db.one("SELECT min(not_before) FROM download WHERE state = 'queued' AND not_before IS NOT NULL")
        return row[0] if row and row[0] is not None else None

    def progress(self, download_id: int, done: int, total: int | None) -> None:
        self._db.run("UPDATE download SET bytes_done = ?, bytes_total = ? WHERE id = ?", (done, total, download_id))

    def finish(self, download_id: int, size: int, path: str | None = None) -> None:
        self._db.run(
            "UPDATE download SET state = 'done', bytes_done = ?, bytes_total = ?, finished_at = ?, error = NULL,"
            " not_before = NULL, path = coalesce(?, path) WHERE id = ?",
            (size, size, self._now(), path, download_id),
        )

    def fail(self, download_id: int, error: str, *, code: str | None = None, retry_at: int | None = None) -> None:
        if retry_at is None:
            self._db.run(
                "UPDATE download SET state = 'failed', error = ?, error_code = ?, finished_at = ? WHERE id = ?",
                (error, code, self._now(), download_id),
            )
        else:
            self._db.run(
                "UPDATE download SET state = 'queued', error = ?, error_code = ?, not_before = ? WHERE id = ?",
                (error, code, retry_at, download_id),
            )

    def wait(self, download_id: int, error: str, *, code: str) -> None:
        """Back into the queue, uncounted: its folder cannot be written, it goes on once the folder works again."""
        self._db.run(
            "UPDATE download SET state = 'queued', error = ?, error_code = ?, not_before = NULL,"
            " attempts = max(attempts - 1, 0) WHERE id = ?",
            (error, code, download_id),
        )

    def cancel(self, download_id: int) -> bool:
        changed, _ = self._db.run(
            "UPDATE download SET state = 'cancelled', finished_at = ? WHERE id = ? AND state IN ('queued', 'running')",
            (self._now(), download_id),
        )
        return changed > 0

    def retry(self, download_id: int) -> bool:
        changed, _ = self._db.run(
            "UPDATE download SET state = 'queued', not_before = NULL, attempts = 0, error = NULL, error_code = NULL,"
            f" finished_at = NULL, position = {_NEXT_POSITION} WHERE id = ? AND state IN ('failed', 'cancelled')",
            (download_id,),
        )
        return changed > 0

    def move(self, download_id: int, index: int) -> bool:
        """Put a queued download at ``index`` of the queue (0 = starts next); the others keep their order."""
        with self._db.transaction() as con:
            rows = con.execute("SELECT id FROM download WHERE state = 'queued' ORDER BY position, id").fetchall()
            ids = [r[0] for r in rows]
            if download_id not in ids:
                return False
            ids.remove(download_id)
            ids.insert(max(0, min(index, len(ids))), download_id)
            con.executemany("UPDATE download SET position = ? WHERE id = ?", [(i + 1, d) for i, d in enumerate(ids)])
        return True

    def delete(self, download_id: int) -> bool:
        changed, _ = self._db.run(
            "DELETE FROM download WHERE id = ? AND state IN ('done', 'failed', 'cancelled')", (download_id,)
        )
        return changed > 0

    def forget_given_up(self, subscription_id: int) -> int:
        """A subscription's failed and cancelled downloads leave the history: its next check loads them anew."""
        changed, _ = self._db.run(
            "DELETE FROM download WHERE subscription_id = ? AND state IN ('failed', 'cancelled')", (subscription_id,)
        )
        return changed

    def requeue_running(self) -> int:
        """At start: what was running when the process ended continues (the .part files allow resuming)."""
        changed, _ = self._db.run("UPDATE download SET state = 'queued' WHERE state = 'running'")
        return changed
