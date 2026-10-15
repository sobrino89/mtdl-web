"""Libraries: the media folders mtdl-web loads into, like the root folders of Sonarr and
Radarr. Each has a kind (series or movies), a folder inside the container, a naming template and, for the rescan,
how Plex sees the folder. A subscription for a series names its series library; films go to a movie library.

Downloads into a library keep their path relative to it, so a library may move (another mount) and everything
follows. Up to 0.1.0.dev19 there was one series library, MTDL_SHOWS_DIR, and downloads kept absolute paths:
``adopt`` turns that state into library "Serien" once, at every start idempotently (existing
subscriptions must not go wrong).
"""

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from mtdl_web.library import DEFAULT_TEMPLATE
from mtdl_web.state import AppSettings, StateStore

Kind = Literal["series", "movies"]
# FileBot's layout in a real movie folder (13.10.2026): "Crime 101 (2026) {tmdb-1171145}/Crime 101 (2026).mkv".
DEFAULT_MOVIE_TEMPLATE = "{titel} ({jahr}) {tmdb}/{titel} ({jahr})"
DEFAULT_TEMPLATES: dict[Kind, str] = {"series": DEFAULT_TEMPLATE, "movies": DEFAULT_MOVIE_TEMPLATE}


@dataclass(frozen=True, slots=True)
class Library:
    id: int
    name: str
    kind: Kind
    path: Path
    template: str
    normalize: bool  # simplify names (umlauts, symbols) as in the downloads folder; off: names as on TMDB
    plex_path: str | None  # the folder as Plex sees it, when the mount differs; None: the same path
    plex_refresh: bool  # Plex reads the folder again after each download into it
    position: int


@dataclass(frozen=True, slots=True)
class LibraryDraft:
    name: str
    kind: Kind
    path: Path
    template: str
    normalize: bool = False
    plex_path: str | None = None
    plex_refresh: bool = False


class LibraryInUse(Exception):
    def __init__(self, subscriptions: list[int], downloads: int) -> None:
        super().__init__(f"used by subscriptions {subscriptions} and {downloads} unfinished downloads")
        self.subscriptions = subscriptions
        self.downloads = downloads


_COLUMNS = "id, name, kind, path, template, normalize, plex_path, plex_refresh, position"
_USED_BY = "SELECT id FROM subscription WHERE json_extract(naming, '$.library_id') = ? ORDER BY id"


def _row(row: tuple[Any, ...]) -> Library:
    lid, name, kind, path, template, normalize, plex_path, plex_refresh, position = row
    return Library(lid, name, kind, Path(path), template, bool(normalize), plex_path, bool(plex_refresh), position)


class LibraryStore:
    def __init__(self, state: StateStore) -> None:
        self._db = state.db

    def entries(self, kind: Kind | None = None) -> list[Library]:
        sql = f"SELECT {_COLUMNS} FROM library"
        params: tuple[Any, ...] = ()
        if kind:
            sql += " WHERE kind = ?"
            params = (kind,)
        return [_row(r) for r in self._db.all(sql + " ORDER BY position, id", params)]

    def get(self, library_id: int) -> Library | None:
        row = self._db.one(f"SELECT {_COLUMNS} FROM library WHERE id = ?", (library_id,))
        return None if row is None else _row(row)

    def only(self, kind: Kind) -> Library | None:
        """The library of a kind when there is exactly one (what an old "target: library" means)."""
        found = self.entries(kind)
        return found[0] if len(found) == 1 else None

    def add(self, draft: LibraryDraft) -> Library:
        _, library_id = self._db.run(
            "INSERT INTO library (name, kind, path, template, normalize, plex_path, plex_refresh, position,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, (SELECT coalesce(max(position), 0) + 1 FROM library), ?)",
            (*self._values(draft), int(time.time())),
        )
        added = self.get(int(library_id or 0))
        assert added is not None
        return added

    def update(self, library_id: int, draft: LibraryDraft) -> Library | None:
        changed, _ = self._db.run(
            "UPDATE library SET name = ?, kind = ?, path = ?, template = ?, normalize = ?, plex_path = ?,"
            " plex_refresh = ? WHERE id = ?",
            (*self._values(draft), library_id),
        )
        return self.get(library_id) if changed else None

    def subscriptions(self, library_id: int) -> list[int]:
        """The subscriptions that load into the library."""
        return [r[0] for r in self._db.all(_USED_BY, (library_id,))]

    def delete(self, library_id: int) -> bool:
        """Refused while a subscription loads into the library or a download into it is unfinished. Finished
        downloads keep their file's absolute path (the history shows where it went); re-adding the folder as a
        library makes them relative again (adopt)."""
        with self._db.transaction() as con:
            row = con.execute("SELECT path FROM library WHERE id = ?", (library_id,)).fetchone()
            if row is None:
                return False
            subs = [r[0] for r in con.execute(_USED_BY, (library_id,))]
            (open_downloads,) = con.execute(
                "SELECT count(*) FROM download WHERE library_id = ? AND state IN ('queued', 'running', 'failed')",
                (library_id,),
            ).fetchone()
            if subs or open_downloads:
                raise LibraryInUse(subs, int(open_downloads))
            for did, path in con.execute(
                "SELECT id, path FROM download WHERE library_id = ?", (library_id,)
            ).fetchall():
                con.execute(
                    "UPDATE download SET library_id = NULL, path = ? WHERE id = ?", (str(Path(row[0], path)), did)
                )
            con.execute("DELETE FROM library WHERE id = ?", (library_id,))
        return True

    @staticmethod
    def _values(draft: LibraryDraft) -> tuple[Any, ...]:
        return (
            draft.name.strip(),
            draft.kind,
            str(draft.path),
            draft.template,
            int(draft.normalize),
            draft.plex_path or None,
            int(draft.plex_refresh),
        )

    def adopt(self, shows_dir: Path | None, settings: AppSettings) -> Library | None:
        """The state before libraries, made into library "Serien" (idempotent, one transaction):

        - MTDL_SHOWS_DIR becomes the series library, with the settings that applied to it (template, simplified
          names, Plex rescan and path), unless a library has that folder already or a series library exists. Once
          per folder: a library deleted in the settings does not come back at the next start;
        - a subscription with target "library" and no library gets the only series library;
        - a download with an absolute path inside a library gets that library and a path relative to it.
        """
        select = f"SELECT {_COLUMNS} FROM library ORDER BY position, id"
        with self._db.transaction() as con:
            libraries = [_row(r) for r in con.execute(select)]
            seeded = con.execute("SELECT value FROM setting WHERE key = 'library_seeded'").fetchone()
            if shows_dir is not None and (seeded is None or json.loads(seeded[0]) != str(shows_dir)):
                if not any(lib.path == shows_dir or lib.kind == "series" for lib in libraries):
                    con.execute(
                        "INSERT INTO library (name, kind, path, template, normalize, plex_path, plex_refresh,"
                        " position, created_at) VALUES ('Serien', 'series', ?, ?, ?, ?, ?, 1, ?)",
                        (
                            str(shows_dir),
                            settings.library_template,
                            int(settings.library_normalize),
                            settings.plex_library_path,
                            int(settings.plex_refresh),
                            int(time.time()),
                        ),
                    )
                    libraries = [_row(r) for r in con.execute(select)]
                con.execute(
                    "INSERT INTO setting (key, value) VALUES ('library_seeded', ?)"
                    " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                    (json.dumps(str(shows_dir)),),
                )
            series = [lib for lib in libraries if lib.kind == "series"]
            if len(series) == 1:
                for sid, naming in con.execute("SELECT id, naming FROM subscription").fetchall():
                    data = json.loads(naming)
                    if data.get("target") == "library" and data.get("library_id") is None:
                        data["library_id"] = series[0].id
                        con.execute(
                            "UPDATE subscription SET naming = ? WHERE id = ?",
                            (json.dumps(data, ensure_ascii=False), sid),
                        )
            for did, path in con.execute(
                "SELECT id, path FROM download WHERE library_id IS NULL AND path LIKE '/%'"
            ).fetchall():
                for lib in libraries:
                    if Path(path).is_relative_to(lib.path):
                        con.execute(
                            "UPDATE download SET library_id = ?, path = ? WHERE id = ?",
                            (lib.id, str(Path(path).relative_to(lib.path)), did),
                        )
                        break
        return series[0] if len(series) == 1 else None
