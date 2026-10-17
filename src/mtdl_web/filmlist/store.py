"""Local film index: one SQLite file with the films and an FTS5 index.

The film list is a cache that can always be rebuilt from the server, so it lives in its own database file, apart
from the application state. An import holds the write lock for its whole run; other writers are not blocked.
"""

import contextlib
import datetime as dt
import logging
import re
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

from mtdl_web.db import Database, connect, migrate, transaction
from mtdl_web.filmlist.parser import ListMeta
from mtdl_web.filmlist.prepared import Prepared
from mtdl_web.models import Film, Scope, SearchResult
from mtdl_web.query import Query, parse
from mtdl_web.text import index_text, search_text

log = logging.getLogger(__name__)

_FTS_DDL = """
CREATE VIRTUAL TABLE film_fts USING fts5(
    topic, title, description,
    content='', contentless_delete=1,
    tokenize='unicode61 remove_diacritics 0'
);
"""

MIGRATIONS = (
    """
    CREATE TABLE film (
        pk INTEGER PRIMARY KEY,  -- ascending, so imports append instead of inserting at random places
        id INTEGER NOT NULL UNIQUE,  -- models.film_id, stable across imports
        identity INTEGER NOT NULL,  -- hash of channel, topic, url, website: replacement key for diffs
        channel TEXT NOT NULL,
        topic TEXT NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        timestamp INTEGER,
        duration INTEGER,
        size INTEGER,
        url TEXT,
        url_low TEXT,
        url_hd TEXT,
        url_subtitle TEXT,
        url_website TEXT,
        geo TEXT NOT NULL,
        content_hash INTEGER NOT NULL,
        first_seen INTEGER NOT NULL
    ) STRICT;
    CREATE INDEX film_identity ON film (identity);
    CREATE INDEX film_first_seen ON film (first_seen);
    CREATE INDEX film_timestamp ON film (timestamp);
    CREATE TABLE list_import (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL CHECK (kind IN ('full', 'diff')),
        list_id TEXT NOT NULL,
        created INTEGER NOT NULL,
        imported_at INTEGER NOT NULL,
        added INTEGER NOT NULL,
        changed INTEGER NOT NULL,
        removed INTEGER NOT NULL,
        total INTEGER NOT NULL
    ) STRICT;
    CREATE TABLE setting (key TEXT PRIMARY KEY, value TEXT NOT NULL) STRICT;
    """
    + _FTS_DDL,
)

_FIELDS = (
    "id",
    "channel",
    "topic",
    "title",
    "description",
    "timestamp",
    "duration",
    "size",
    "url",
    "url_low",
    "url_hd",
    "url_subtitle",
    "url_website",
    "geo",
)
_SELECT = "SELECT " + ", ".join(f"film.{f}" for f in _FIELDS) + " FROM film"
_INSERT = (
    f"INSERT INTO film ({', '.join(_FIELDS)}, identity, content_hash, first_seen)"
    f" VALUES ({', '.join('?' * (len(_FIELDS) + 3))})"
)
_UPDATE = f"UPDATE film SET {', '.join(f'{f} = ?' for f in _FIELDS[1:])}, content_hash = ? WHERE pk = ?"
_WORD = re.compile(r"[^\W_]+")
# How film_fts stores text (``text.index_text``); a new value rebuilds the index at the next start.
FTS_TEXT = "fold-2"

Kind = Literal["full", "diff"]
_UNCHANGED, _ADDED, _CHANGED = range(3)
Progress = Callable[[int], None]  # films read so far
PROGRESS_EVERY = 5000


class ImportSource(Protocol):
    """A film list to import: ``FilmListReader`` (sequential) or ``ParallelFilmListReader``."""

    @property
    def meta(self) -> ListMeta: ...

    def prepared(self) -> Iterator[Prepared]: ...


class ImportRejected(Exception):
    """A full list that would remove too much of the index; most likely a broken list on the server."""


@dataclass(frozen=True, slots=True)
class ImportResult:
    kind: Kind
    list_id: str
    created: dt.datetime
    added: int
    changed: int
    removed: int
    total: int  # entries in the imported list
    seconds: float


def url_folder(url: str) -> str:
    """The path of a video url without host and file name: ``/content/75/58/<uuid>``. Broadcasters keep one folder
    per broadcast (with the qualities as files), hosts change (rbbmediapmdp-a.akamaihd.net → rbb-progressive…)."""
    return urlsplit(url).path.rsplit("/", 1)[0]


def _film(row: tuple[Any, ...]) -> Film:
    (fid, channel, topic, title, description, timestamp, duration, size, url, low, hd, sub, web, geo) = row
    return Film(
        id=fid,
        channel=channel,
        topic=topic,
        title=title,
        description=description,
        timestamp=timestamp,
        duration=duration,
        size=size,
        url=url,
        url_low=low,
        url_hd=hd,
        url_subtitle=sub,
        url_website=web,
        geo=tuple(geo.split("-")) if geo else (),
    )


_COLUMNS_FOR = {
    "topic_title": "{topic title}",
    "title": "{title}",
    "topic": "{topic}",
    "all": "{topic title description}",
}


def _all_of(words: Iterable[str]) -> str | None:
    found = _folded_words(" ".join(words))  # like the index: "İstanbul" and decomposed umlauts stay one word
    return "(" + " AND ".join(f'"{w}"*' for w in found) + ")" if found else None


def match_expression(
    query: Query | str, *, include_description: bool = False, scope: Scope = "topic_title"
) -> str | None:
    """FTS5 query with MediathekViewWeb semantics: every word as a word prefix, all words required, in topic or
    title (and description on request; ``scope`` narrows to title or topic), selectors per field (``query.py``).
    None if nothing is searched in the text fields."""
    q = parse(query) if isinstance(query, str) else query
    parts = []
    if free := _all_of(q.words):
        if include_description:
            columns = _COLUMNS_FOR["all"]
        elif scope == "topic_title" and q.topics:
            columns = "{title}"  # MediathekViewWeb: with a topic selector, free words are for the title
        else:
            columns = _COLUMNS_FOR[scope]
        parts.append(f"{columns} : {free}")
    for column, groups in (("topic", q.topics), ("title", q.titles), ("description", q.descriptions)):
        either = [f"{{{column}}} : {words}" for group in groups if (words := _all_of(group))]
        if either:
            parts.append("(" + " OR ".join(either) + ")")
    return " AND ".join(parts) or None


def _folded_words(text: str) -> list[str]:
    """Words as the index holds them (``search_text``)."""
    return _WORD.findall(search_text(text))


class FilmStore:
    def __init__(self, path: Path | str, *, index_description: bool, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock  # when an import happened (list_import.imported_at)
        self._con = connect(path)
        self._con.execute("PRAGMA cache_size = -65536")  # 64 MB; the default 2 MB makes a full import crawl
        # A full import is one transaction, so the WAL grows to the size of the database; without a limit it keeps
        # that size on disk after the checkpoint (measured: 868 MB next to 863 MB).
        self._con.execute(f"PRAGMA journal_size_limit = {64 << 20}")
        migrate(self._con, MIGRATIONS)
        self.index_description = index_description
        self.importing = False  # read by the shutdown: an import runs in a worker thread
        self._channels: tuple[str, ...] | None = None  # for channel selectors; reset by every import
        self._imports = 0  # finished imports: a channel list read while one ran is not kept
        self._sync_fts()
        # Queries get their own connection: they see the last committed state while an import runs.
        self._read = Database(path, query_only=True)

    def close(self) -> None:
        self._read.close()
        self._con.close()

    def __enter__(self) -> "FilmStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- import ---------------------------------------------------------------------------------------------------

    @contextlib.contextmanager
    def _importing(self) -> Iterator[None]:
        self.importing = True
        try:
            yield
        finally:
            self.importing = False
            self._imports += 1
            self._channels = None
        # Hand the WAL back to the file system; with readers active this is partial and the limit does the rest.
        self._con.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def import_full(
        self, reader: ImportSource, *, min_ratio: float = 0.5, progress: Progress | None = None
    ) -> ImportResult:
        """``progress`` hears the films read so far, every ``PROGRESS_EVERY`` and at the end."""
        with self._importing():
            return self._import_full(reader, min_ratio=min_ratio, progress=progress)

    def import_diff(self, reader: ImportSource, *, progress: Progress | None = None) -> ImportResult:
        with self._importing():
            return self._import_diff(reader, progress=progress)

    def _import_full(
        self, reader: ImportSource, *, min_ratio: float = 0.5, progress: Progress | None = None
    ) -> ImportResult:
        """Reconcile the index with a full list: add, update, remove. ``first_seen`` of known films survives.

        A list with fewer than ``min_ratio`` times the films already indexed is rejected and nothing changes.
        """
        started = time.monotonic()
        seen_at = int(reader.meta.created.timestamp())
        con = self._con
        con.execute("CREATE TEMP TABLE IF NOT EXISTS seen (id INTEGER PRIMARY KEY)")
        added = changed = total = 0
        with transaction(con):
            con.execute("DELETE FROM temp.seen")
            before = self.count()
            batch: list[tuple[object]] = []
            for row in reader.prepared():
                total += 1
                batch.append((row.values[0],))
                if len(batch) == 10_000:
                    con.executemany("INSERT OR IGNORE INTO temp.seen VALUES (?)", batch)
                    batch.clear()
                outcome = self._upsert(row, seen_at)
                added += outcome == _ADDED
                changed += outcome == _CHANGED
                if progress is not None and total % PROGRESS_EVERY == 0:
                    progress(total)
            con.executemany("INSERT OR IGNORE INTO temp.seen VALUES (?)", batch)
            if progress is not None:
                progress(total)
            if before and total < min_ratio * before:
                raise ImportRejected(f"full list has {total} films, the index {before}")
            removed = self._delete_where("id NOT IN (SELECT id FROM temp.seen)")
            result = self._log("full", reader, added, changed, removed, total, started)
        con.execute("DELETE FROM temp.seen")
        return result

    def _import_diff(self, reader: ImportSource, *, progress: Progress | None = None) -> ImportResult:
        """Merge a diff list like the Java client: films with the same identity (channel, topic, url, website) are
        replaced, all others added. Nothing else is removed; that only happens with the next full list."""
        started = time.monotonic()
        seen_at = int(reader.meta.created.timestamp())
        added = changed = removed = total = 0
        with transaction(self._con):
            for row in reader.prepared():
                total += 1
                fid, channel, topic = row.values[:3]
                removed += self._delete_where(
                    "identity = ? AND channel = ? AND topic = ? AND url IS ? AND url_website IS ? AND id != ?",
                    (row.identity, channel, topic, row.values[8], row.values[12], fid),
                )
                outcome = self._upsert(row, seen_at)
                added += outcome == _ADDED
                changed += outcome == _CHANGED
                if progress is not None and total % PROGRESS_EVERY == 0:
                    progress(total)
            if progress is not None:
                progress(total)
            return self._log("diff", reader, added, changed, removed, total, started)

    def _upsert(self, prepared: Prepared, seen_at: int) -> int:
        con = self._con
        values, new_hash = prepared.values, prepared.content_hash
        row = con.execute("SELECT pk, content_hash FROM film WHERE id = ?", (values[0],)).fetchone()
        if row is not None and row[1] == new_hash:
            return _UNCHANGED
        if row is None:
            pk = con.execute(_INSERT, (*values, prepared.identity, new_hash, seen_at)).lastrowid
        else:
            pk = row[0]
            con.execute(_UPDATE, (*values[1:], new_hash, pk))
            con.execute("DELETE FROM film_fts WHERE rowid = ?", (pk,))
        description = index_text(str(values[4])) if self.index_description else ""
        con.execute(
            "INSERT INTO film_fts (rowid, topic, title, description) VALUES (?, ?, ?, ?)",
            (pk, index_text(str(values[2])), index_text(str(values[3])), description),
        )
        return _ADDED if row is None else _CHANGED

    def _delete_where(self, condition: str, params: tuple[object, ...] = ()) -> int:
        self._con.execute(f"DELETE FROM film_fts WHERE rowid IN (SELECT pk FROM film WHERE {condition})", params)
        return self._con.execute(f"DELETE FROM film WHERE {condition}", params).rowcount

    def _log(
        self, kind: Kind, reader: ImportSource, added: int, changed: int, removed: int, total: int, started: float
    ) -> ImportResult:
        result = ImportResult(
            kind=kind,
            list_id=reader.meta.list_id,
            created=reader.meta.created,
            added=added,
            changed=changed,
            removed=removed,
            total=total,
            seconds=time.monotonic() - started,
        )
        self._con.execute(
            "INSERT INTO list_import (kind, list_id, created, imported_at, added, changed, removed, total)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (kind, result.list_id, int(result.created.timestamp()), int(self._clock()), added, changed, removed, total),
        )
        return result

    def _sync_fts(self) -> None:
        """Rebuild the full-text index when the description setting or the stored text form (``FTS_TEXT``) changed
        since the last start."""
        wanted = {"fts_description": "1" if self.index_description else "0", "fts_text": FTS_TEXT}
        keys = ", ".join(f"'{k}'" for k in wanted)
        stored = dict(self._con.execute(f"SELECT key, value FROM setting WHERE key IN ({keys})").fetchall())
        if stored == wanted:
            return
        with transaction(self._con):
            if self._con.execute("SELECT 1 FROM film LIMIT 1").fetchone() is not None:
                started = time.monotonic()
                self._con.create_function("index_text", 1, index_text, deterministic=True)
                self._con.execute("DROP TABLE film_fts")
                self._con.execute(_FTS_DDL)
                self._con.execute(
                    "INSERT INTO film_fts (rowid, topic, title, description) SELECT pk, index_text(topic),"
                    " index_text(title), CASE WHEN ? THEN index_text(description) ELSE '' END FROM film",
                    (self.index_description,),
                )
                log.info("full-text index rebuilt in %.0f s", time.monotonic() - started)
            self._con.executemany("INSERT OR REPLACE INTO setting (key, value) VALUES (?, ?)", wanted.items())

    # -- queries --------------------------------------------------------------------------------------------------

    def count(self) -> int:
        (n,) = self._read.one("SELECT count(*) FROM film") or (0,)
        return int(n)

    def get(self, film_id: int) -> Film | None:
        row = self._read.one(f"{_SELECT} WHERE id = ?", (film_id,))
        return None if row is None else _film(row)

    def already_imported(self, list_id: str, kind: Kind) -> bool:
        """A full list counts as imported only by a full import; a diff also by the full list it came with."""
        sql = "SELECT 1 FROM list_import WHERE list_id = ? AND (kind = 'full' OR ? = 'diff') LIMIT 1"
        return self._read.one(sql, (list_id, kind)) is not None

    def last_full_at(self) -> dt.datetime | None:
        """When the newest full list was imported: the next one is due once a day (fetch.plan)."""
        row = self._read.one("SELECT imported_at FROM list_import WHERE kind = 'full' ORDER BY id DESC LIMIT 1")
        return None if row is None else dt.datetime.fromtimestamp(row[0], dt.UTC)

    def last_import(self, kind: Kind | None = None) -> ImportResult | None:
        sql = "SELECT kind, list_id, created, added, changed, removed, total FROM list_import"
        params: tuple[object, ...] = ()
        if kind is not None:
            sql += " WHERE kind = ?"
            params = (kind,)
        row = self._read.one(sql + " ORDER BY id DESC LIMIT 1", params)
        if row is None:
            return None
        k, list_id, created, added, changed, removed, total = row
        return ImportResult(
            kind=k,
            list_id=list_id,
            created=dt.datetime.fromtimestamp(created, dt.UTC),
            added=added,
            changed=changed,
            removed=removed,
            total=total,
            seconds=0.0,
        )

    def search(
        self,
        query: str,
        *,
        include_description: bool = False,
        scope: Scope = "topic_title",
        limit: int = 50,
        offset: int = 0,
    ) -> SearchResult:
        """Newest first. An empty query lists all films. ``query`` may use the MediathekViewWeb syntax."""
        q = parse(query)
        if (include_description or scope == "all" or q.descriptions) and not self.index_description:
            raise ValueError("descriptions are not indexed (MTDL_INDEX_DESCRIPTION is off)")
        expression = match_expression(q, include_description=include_description, scope=scope)
        where, params = self._conditions(q)
        order = " ORDER BY film.timestamp DESC NULLS LAST, film.id LIMIT ? OFFSET ?"
        if expression is None and not where:
            rows = self._read.all(_SELECT + order, (limit, offset))
            return SearchResult([_film(r) for r in rows], self.count())
        if expression is None:
            sql, args = " WHERE " + " AND ".join(where), params
        else:
            sql = " JOIN film_fts ON film_fts.rowid = film.pk WHERE " + " AND ".join(["film_fts MATCH ?", *where])
            args = [expression, *params]
        rows = self._read.all(_SELECT + sql + order, (*args, limit, offset))
        if expression is not None and not where:  # the fast count: the index alone
            (total,) = self._read.one("SELECT count(*) FROM film_fts WHERE film_fts MATCH ?", (expression,)) or (0,)
        else:
            (total,) = self._read.one("SELECT count(*) FROM film" + sql, args) or (0,)
        return SearchResult([_film(r) for r in rows], total)

    def channels(self) -> tuple[str, ...]:
        if self._channels is not None:
            return self._channels
        imports = self._imports
        found = tuple(c for (c,) in self._read.all("SELECT DISTINCT channel FROM film ORDER BY 1"))
        if imports == self._imports and not self.importing:
            self._channels = found
        return found

    def _conditions(self, q: Query) -> tuple[list[str], list[object]]:
        """SQL conditions on ``film`` for channel selectors and duration bounds."""
        where: list[str] = []
        params: list[object] = []
        if groups := [words for group in q.channels if (words := _folded_words(" ".join(group)))]:
            names = [
                name
                for name in self.channels()
                if any(all(any(w.startswith(p) for w in _folded_words(name)) for p in words) for words in groups)
            ]
            where.append(f"film.channel IN ({', '.join('?' * len(names))})" if names else "0")
            params += names
        if q.min_seconds is not None:
            where.append("film.duration >= ?")
            params.append(q.min_seconds)
        if q.max_seconds is not None:
            where.append("film.duration <= ?")
            params.append(q.max_seconds)
        return where, params

    def by_urls(self, urls: Iterable[str]) -> dict[str, Film]:
        """Films whose normal, low or HD url is one of ``urls``. A table scan: for one-off lookups (takeover)."""
        wanted = list(dict.fromkeys(u for u in urls if u))
        found: dict[str, Film] = {}
        for start in range(0, len(wanted), 300):
            chunk = wanted[start : start + 300]
            marks = ", ".join("?" * len(chunk))
            sql = f"{_SELECT} WHERE url IN ({marks}) OR url_hd IN ({marks}) OR url_low IN ({marks})"
            chosen = set(chunk)
            for row in self._read.all(sql, (*chunk, *chunk, *chunk)):
                film = _film(row)
                for url in (film.url, film.url_hd, film.url_low):
                    if url in chosen:
                        found.setdefault(url, film)
        return found

    def by_url_folders(self, folders: Iterable[str], *, chunk: int = 50_000) -> dict[str, list[Film]]:
        """Films with a video url in one of ``folders`` (see ``url_folder``): finds the broadcast behind a quality
        variant the list no longer names (``…_avc720.mp4`` while it lists 540 and 1080), also after the CDN host
        changed. Scans the table in chunks; for one-off lookups (takeover)."""
        wanted = {f for f in folders if f}
        ids: dict[str, set[int]] = {}
        last = 0
        while wanted:
            rows = self._read.all(
                "SELECT pk, id, url, url_hd, url_low FROM film WHERE pk > ? ORDER BY pk LIMIT ?", (last, chunk)
            )
            if not rows:
                break
            for pk, film_id, *urls in rows:
                last = pk
                for url in urls:
                    if url and (folder := url_folder(url)) in wanted:
                        ids.setdefault(folder, set()).add(film_id)
        out: dict[str, list[Film]] = {}
        for folder, found in ids.items():
            out[folder] = [film for film_id in sorted(found) if (film := self.get(film_id)) is not None]
        return out

    def new_since(self, since: dt.datetime, *, limit: int = 1000) -> list[Film]:
        """Films that entered the index after ``since`` (list creation time), newest broadcast first."""
        rows = self._read.all(
            _SELECT + " WHERE first_seen > ? ORDER BY timestamp DESC NULLS LAST, id LIMIT ?",
            (int(since.timestamp()), limit),
        )
        return [_film(r) for r in rows]

    def database_bytes(self) -> int:
        (pages,) = self._read.one("PRAGMA page_count") or (0,)
        (size,) = self._read.one("PRAGMA page_size") or (0,)
        return int(pages) * int(size)
