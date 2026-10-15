"""Series in the media library: which TMDB episode a Mediathek entry is, where it goes, whether it is there.

Three signals, strongest first (measured 13.10.2026 on Die Anstalt, Werwölfe and Das Gipfeltreffen against TMDB,
test/test_library.py):

- The episode marker at the end of the title. "(S02/E03)" names season and episode; "(S2026/E08)" names a year,
  which is the TMDB season that started in it (Die Anstalt 2026 = season 13); "(58)" counts across the regular
  seasons (Das Gipfeltreffen 58 = season 8, episode 8).
- The TMDB episode name at the start of the title, after "Folge 3:" and quotes ("Die Prüf-Anstalt - Politsatire
  mit …" = "Die Prüf-Anstalt").
- The broadcast day equal to the TMDB air date. Only a hint: ARD put Werwölfe online up to three weeks before the
  day TMDB lists, so the day decides alone only with a fitting length and no other candidate.

A season/episode marker that contradicts the episode name leaves the entry unmatched, and so does a clip much
shorter than the episode: better no file than a wrong one in the library.
"""

import datetime as dt
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

from mtdl_web.metadata import EpisodeInfo, MovieInfo, SeriesInfo
from mtdl_web.models import Film
from mtdl_web.naming import _PLACEHOLDER, render_template
from mtdl_web.text import fold

# FileBot's layout in a real library (13.10.2026):
# "Die Anstalt {tmdb-73860}/Season 13/Die Anstalt - S13E08 - <name>.mp4", specials in "Specials". Plex reads the ID
# in the folder name; IMDb works there too, but numbers come from TMDB.
DEFAULT_TEMPLATE = "{serie} {tmdb}/{staffelordner}/{serie} - S{staffel}E{folge} - {folgentitel}"
ALIASES = {
    "serie": "show",
    "serienjahr": "show_year",
    "staffel": "season",
    "folge": "episode",
    "folgentitel": "episode_title",
    "staffelordner": "season_folder",
    "titel": "title",
    "datum": "date",
    "jahr": "year",
    "sender": "channel",
}
KNOWN = {"show", "show_year", "tmdb", "tvdb", "imdb", "season", "episode", "episode_title", "season_folder"}
KNOWN |= {"title", "date", "year", "channel", "ext"}
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".m4v", ".avi", ".mov", ".ts", ".webm", ".wmv", ".mpg", ".mpeg"}
STAGING = ".mtdl-incoming"  # unfinished files inside the library, hidden from the media server
# In the staging folder: Plex skips a folder by .plexignore, Jellyfin and Emby by .ignore (Jellyfin also skips
# hidden folders, but its file watcher looks at the file that changed; .ignore covers that too). Both always: one
# library may serve several media servers.
IGNORE_FILES = (".plexignore", ".ignore")

_MARKER = re.compile(
    r"\s*\((?:S(?P<season>\d{1,4})/E(?P<episode>\d{1,4})|Staffel (?P<season2>\d{1,4}), Folge (?P<episode2>\d{1,4})"
    r"|(?P<absolute>\d{1,4}))\)\s*$"
)
_LEADING_NUMBER = re.compile(r"^\s*(?:Folge|Teil|Episode)\s+\d+\s*[:.]\s*", re.IGNORECASE)
_PLACEHOLDER_NAME = re.compile(r"^(?:folge|episode|teil)\s+\d+$")  # TMDB entries waiting for a real name
_NOT_WORD = re.compile(r"\W+")
_SEASON_EPISODE = re.compile(r"(?<![A-Za-z0-9])S(\d{1,4})E(\d{1,4})", re.IGNORECASE)
_MIN_PREFIX = 8  # characters an episode name needs to count as the start of a longer title


def words(text: str) -> str:
    """Comparison text: no case, no punctuation, single spaces ("Die Anstalt – Jetzt…" = "Die Anstalt - Jetzt…")."""
    return " ".join(_NOT_WORD.sub(" ", fold(text)).split())


@dataclass(frozen=True, slots=True)
class Marker:
    season: int | None = None
    episode: int | None = None
    absolute: int | None = None


def marker(title: str) -> tuple[Marker | None, str]:
    """The episode marker at the end of a title and the title without it."""
    m = _MARKER.search(title)
    if m is None:
        return None, title
    rest = title[: m.start()]
    if m["season"] or m["season2"]:
        return Marker(int(m["season"] or m["season2"]), int(m["episode"] or m["episode2"])), rest
    return Marker(absolute=int(m["absolute"])), rest


@dataclass(frozen=True, slots=True)
class EpisodeMatch:
    """``episode`` None: no TMDB episode, ``reason`` says why (no_series, no_match, ambiguous, conflict,
    too_short). Else the signals that agreed: number, number_title, number_date, title, title_date, date."""

    episode: EpisodeInfo | None
    reason: str


def _season_of_year(series: SeriesInfo, year: int) -> int | None:
    starts: dict[int, dt.date] = {}
    for e in series.episodes:
        if e.season > 0 and e.air_date and (e.season not in starts or e.air_date < starts[e.season]):
            starts[e.season] = e.air_date
    started = [s for s, day in starts.items() if day.year == year]
    if len(started) == 1:
        return started[0]
    running = {e.season for e in series.episodes if e.season > 0 and e.air_date and e.air_date.year == year}
    return next(iter(running)) if len(running) == 1 else None


def _absolute(series: SeriesInfo, number: int) -> EpisodeInfo | None:
    regular = sorted((e for e in series.episodes if e.season > 0), key=lambda e: (e.season, e.number))
    return regular[number - 1] if 0 < number <= len(regular) else None


def _by_title(rest: str, series: SeriesInfo) -> list[EpisodeInfo]:
    text = words(_LEADING_NUMBER.sub("", rest))
    show = words(series.name)
    texts = [text]
    if show and text.startswith(show + " "):  # "Werwölfe · Das Spiel …: <episode>"
        texts.append(text[len(show) + 1 :])
    exact: list[EpisodeInfo] = []
    prefix: list[tuple[int, EpisodeInfo]] = []
    for e in series.episodes:
        name = words(e.name)
        if not name or _PLACEHOLDER_NAME.match(name):
            continue
        if any(t == name for t in texts):
            exact.append(e)
        elif len(name) >= _MIN_PREFIX and name != show and any(t.startswith(name + " ") for t in texts):
            prefix.append((len(name), e))
    if exact:
        return exact
    longest = max((n for n, _ in prefix), default=0)
    return [e for n, e in prefix if n == longest]


def _too_short(film: Film, episode: EpisodeInfo) -> bool:
    """A clip of a few minutes is no episode of 45 (ZDF puts scenes of Die Anstalt online on their own)."""
    return bool(film.duration and episode.runtime and film.duration < episode.runtime * 30)


def _fits(film: Film, episode: EpisodeInfo) -> bool:
    """The day alone decides only with both lengths known and close: TMDB lists no runtime for older Anstalt
    episodes, and four clips of 2 to 8 minutes share the day of one of them (1.11.2016)."""
    if not film.duration or not episode.runtime:
        return False
    return abs(film.duration / 60 - episode.runtime) <= max(5, 0.25 * episode.runtime)


def match_episode(film: Film, series: SeriesInfo, timezone: dt.tzinfo) -> EpisodeMatch:
    found, rest = marker(film.title)
    day = dt.datetime.fromtimestamp(film.timestamp, timezone).date() if film.timestamp is not None else None
    by_number = {(e.season, e.number): e for e in series.episodes}
    numbered: EpisodeInfo | None = None
    if found and found.season is not None and found.episode is not None:
        season = found.season if found.season in series.seasons else None
        if season is None and found.season >= 1900:
            season = _season_of_year(series, found.season)
        numbered = by_number.get((season, found.episode)) if season is not None else None
    elif found and found.absolute is not None:
        numbered = _absolute(series, found.absolute)
    titled = _by_title(rest, series)
    dated = [e for e in series.episodes if day is not None and e.air_date == day]

    def checked(episode: EpisodeInfo, reason: str) -> EpisodeMatch:
        return EpisodeMatch(None, "too_short") if _too_short(film, episode) else EpisodeMatch(episode, reason)

    if numbered is not None and (not titled or numbered in titled):
        reason = "number_title" if numbered in titled else "number_date" if numbered in dated else "number"
        return checked(numbered, reason)
    if numbered is not None and found and found.absolute is None:
        return EpisodeMatch(None, "conflict")  # "(S02/E03)" says one episode, the name another
    if titled:
        if len(titled) == 1:
            return checked(titled[0], "title_date" if titled[0] in dated else "title")
        same_day = [e for e in titled if e in dated]
        if len(same_day) == 1:
            return checked(same_day[0], "title_date")
        if found and found.absolute is not None:  # "(3)" may count within the season
            same_number = [e for e in titled if e.number == found.absolute]
            if len(same_number) == 1:
                return checked(same_number[0], "number_title")
        return EpisodeMatch(None, "ambiguous")
    fitting = [e for e in dated if _fits(film, e)]
    if len(fitting) == 1:
        return checked(fitting[0], "date")
    return EpisodeMatch(None, "ambiguous" if fitting else "no_match")


# -- paths in the library ------------------------------------------------------------------------------------------


def unknown_placeholders(template: str, kind: str = "series") -> list[str]:
    aliases, known = (MOVIE_ALIASES, MOVIE_KNOWN) if kind == "movies" else (ALIASES, KNOWN)
    return [m[1] for m in _PLACEHOLDER.finditer(template) if aliases.get(m[1], m[1]) not in known]


# Movies (libraries of kind "movies"): the film's TMDB title and year, like FileBot "Titel (Jahr) {tmdb-…}".
MOVIE_ALIASES = {"titel": "title", "originaltitel": "original_title", "jahr": "year", "sender": "channel"}
MOVIE_ALIASES |= {"datum": "date"}
MOVIE_KNOWN = {"title", "original_title", "year", "tmdb", "imdb", "channel", "date", "ext"}


def movie_path(
    film: Film,
    movie: MovieInfo,
    template: str,
    *,
    root: Path,
    existing: Path | None,
    normalize: bool,
    timezone: dt.tzinfo,
    ext: str = "mp4",
) -> PurePosixPath:
    """Absolute target of ``film`` as the TMDB movie ``movie`` in a movie library; an existing folder of the movie
    replaces the first part of the template, as for series."""
    day = dt.datetime.fromtimestamp(film.timestamp, timezone).date().isoformat() if film.timestamp is not None else ""
    values = {
        "title": movie.title or movie.original_title or marker(film.title)[1],
        "original_title": movie.original_title or movie.title,
        "year": str(movie.year or ""),
        "tmdb": f"{{tmdb-{movie.tmdb_id}}}",
        "imdb": f"{{imdb-{movie.imdb_id}}}" if movie.imdb_id else "",
        "channel": film.channel,
        "date": day,
        "ext": ext,
    }
    relative = render_template(template, values, MOVIE_ALIASES, normalize=normalize, ext=ext)
    return _placed(relative, root, existing)


def _placed(relative: PurePosixPath, root: Path, existing: Path | None) -> PurePosixPath:
    parts = relative.parts
    if existing is not None and len(parts) > 1:
        return PurePosixPath(existing, *parts[1:])
    return PurePosixPath(root, *parts)


def tags(item: SeriesInfo | MovieInfo) -> dict[str, list[str]]:
    """The ID markers of a series or movie in folder names, Plex style and Jellyfin/Emby style. TMDB numbers series
    and movies separately: a series library holds only series, a movie library only movies."""
    out = {"tmdb": [f"{{tmdb-{item.tmdb_id}}}", f"[tmdbid-{item.tmdb_id}]"]}
    if isinstance(item, SeriesInfo) and item.tvdb_id:
        out["tvdb"] = [f"{{tvdb-{item.tvdb_id}}}", f"[tvdbid-{item.tvdb_id}]"]
    if item.imdb_id:
        out["imdb"] = [f"{{imdb-{item.imdb_id}}}", f"[imdbid-{item.imdb_id}]"]
    return out


def id_folders(root: Path, item: SeriesInfo | MovieInfo) -> list[Path]:
    """Folders of the series or movie in its library, found by an ID in the name (FileBot and Plex write them so).
    Folders without an ID stay out: a name alone may belong to another show."""
    markers = [m for found in tags(item).values() for m in found]
    try:
        entries = [p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")]
    except OSError:
        return []
    return sorted(p for p in entries if any(m in p.name for m in markers))


def preferred_folder(folders: Iterable[Path], item: SeriesInfo | MovieInfo, template: str) -> Path | None:
    """Where new files go when the series or movie has folders already: the one with the ID kind the template
    writes."""
    folders = list(folders)
    if not folders:
        return None
    for kind, markers in tags(item).items():
        if f"{{{kind}}}" in template:
            for folder in folders:
                if any(m in folder.name for m in markers):
                    return folder
    return folders[0]


def episodes_on_disk(folders: Iterable[Path]) -> set[tuple[int, int]]:
    """Season and episode of every video file in the series folders ("… - S02E03 - …", "show.s02e03.mkv")."""
    found: set[tuple[int, int]] = set()
    for folder in folders:
        try:
            files = [p for p in folder.iterdir()]
            files += [q for p in files if p.is_dir() and not p.name.startswith(".") for q in p.iterdir()]
        except OSError:
            continue
        for path in files:
            if path.suffix.lower() in VIDEO_EXTENSIONS:
                found |= {(int(m[1]), int(m[2])) for m in _SEASON_EPISODE.finditer(path.name)}
    return found


class LibraryUnavailable(Exception):
    """A subscription wants the library, but none is configured."""


class ShelfLibrary(Protocol):
    """What a shelf needs of a library (libraries.Library)."""

    @property
    def id(self) -> int: ...
    @property
    def path(self) -> Path: ...
    @property
    def template(self) -> str: ...
    @property
    def normalize(self) -> bool: ...


class Shelf:
    """One series in its library for one preview: its folders and the episodes in them, read once."""

    def __init__(self, series: SeriesInfo | None, library: ShelfLibrary, *, timezone: dt.tzinfo) -> None:
        self.series = series
        self.library = library
        self._root = library.path
        self._template = library.template
        self._normalize = library.normalize
        self._tz = timezone
        self.folders = id_folders(library.path, series) if series else []
        self._existing = preferred_folder(self.folders, series, library.template) if series else None
        self._on_disk = episodes_on_disk(self.folders)

    def match(self, film: Film) -> EpisodeMatch:
        if self.series is None:
            return EpisodeMatch(None, "no_series")
        return match_episode(film, self.series, self._tz)

    def path(self, film: Film, episode: EpisodeInfo) -> PurePosixPath:
        assert self.series is not None
        return library_path(
            film,
            episode,
            self.series,
            self._template,
            root=self._root,
            existing=self._existing,
            normalize=self._normalize,
            timezone=self._tz,
        )

    def has(self, episode: EpisodeInfo, path: PurePosixPath) -> bool:
        """The episode is in the library already, under any name with its "S02E03" (FileBot, other tools)."""
        return (episode.season, episode.number) in self._on_disk or Path(path).exists()


def library_path(
    film: Film,
    episode: EpisodeInfo,
    series: SeriesInfo,
    template: str,
    *,
    root: Path,
    existing: Path | None,
    normalize: bool,
    timezone: dt.tzinfo,
    ext: str = "mp4",
) -> PurePosixPath:
    """Absolute target of ``film`` as ``episode``. An existing series folder replaces the first part of the
    template, so new episodes join the ones there (Plex shows a series in two folders as one, FileBot made such)."""
    _, rest = marker(film.title)
    day = dt.datetime.fromtimestamp(film.timestamp, timezone).date().isoformat() if film.timestamp is not None else ""
    found = tags(series)
    # TMDB lists a new episode as "Folge 1" until someone enters its name (Das Gipfeltreffen season 9, 13.10.2026):
    # then the Mediathek's title names the file; season and number still come from TMDB.
    named = bool(episode.name) and not _PLACEHOLDER_NAME.match(words(episode.name))
    values = {
        "show": series.name,
        "show_year": str(series.year or ""),
        "tmdb": found["tmdb"][0],
        "tvdb": found["tvdb"][0] if "tvdb" in found else "",
        "imdb": found["imdb"][0] if "imdb" in found else "",
        "season": f"{episode.season:02d}",
        "episode": f"{episode.number:02d}",
        "episode_title": episode.name if named or not rest.strip() else rest,
        "season_folder": "Specials" if episode.season == 0 else f"Season {episode.season:02d}",
        "title": rest,
        "date": day,
        "year": day[:4],
        "channel": film.channel,
        "ext": ext,
    }
    relative = render_template(template, values, ALIASES, normalize=normalize, ext=ext)
    return _placed(relative, root, existing)
