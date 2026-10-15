"""Series data from TMDB: name, IDs and the episodes of every season, for library names and numbers.

Access comes from ``MTDL_TMDB_TOKEN`` or the token saved in the settings. A read access token (a JWT, "eyJ…") goes
into the Authorization header; a v3 API key only works as query parameter, which httpx logs (``__main__`` redacts
it). Answers stay in the state database for six hours (TMDB allows six months), so previews and checks cost no
requests; when TMDB fails, an older answer beats none. A refresh asks for the series with its seasons appended: one
request (``_fetch``), four a day per series however many entries wait for an episode TMDB does not list yet: they
are matched again at every check, offline against the cache.

TMDB's rules (developer.themoviedb.org/docs/rate-limiting and /faq, read 13.10.2026): the limit lies "somewhere in
the 40 requests per second range" and a 429 must be respected; free for non-commercial use with the TMDB logo and
the notice "This product uses the TMDB API but is not endorsed or certified by TMDB" in the app (settings page).
mtdl-web asks at most ten times a second, waits on 429 (``Retry-After`` if sent) and identifies itself with its
User-Agent like every request (http.py).
"""

import asyncio
import datetime as dt
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from mtdl_web.db import Database

log = logging.getLogger(__name__)

API = "https://api.themoviedb.org/3"
LANGUAGE = "de-DE"  # the Mediathek is German: episode names as German TV lists them
MAX_AGE = 6 * 3600
TIMEOUT = httpx.Timeout(15.0, connect=10.0)
# Seasons ride along with the series request: TMDB takes at most 20 parts per append_to_response (Travis Bell,
# TMDB staff, themoviedb.org/talk/535bd54fc3a36830a9001648, 27.04.2014; tried on Werwölfe 13.10.2026).
APPEND = 20
RATE = 10.0  # requests per second at most, a quarter of TMDB's limit
RETRIES = 2  # after a 429
RETRY_AFTER_MAX = 30.0
_JWT = re.compile(r"^eyJ[\w-]+\.[\w-]+\.[\w-]+$")


class Pace:
    """Spaces requests evenly: at most ``rate`` per second over all callers."""

    def __init__(self, rate: float) -> None:
        self._interval = 1.0 / rate
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self._interval
        if delay > 0:
            await asyncio.sleep(delay)


class MetadataError(Exception):
    """``code``: no_token, unauthorized, not_found, unavailable."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True, slots=True)
class EpisodeInfo:
    season: int  # 0: specials
    number: int
    name: str
    air_date: dt.date | None
    runtime: int | None  # minutes


@dataclass(frozen=True, slots=True)
class SeriesInfo:
    tmdb_id: int
    name: str
    original_name: str
    first_air_date: dt.date | None
    imdb_id: str | None
    tvdb_id: int | None
    episodes: tuple[EpisodeInfo, ...]
    fetched_at: int = 0

    @property
    def year(self) -> int | None:
        return self.first_air_date.year if self.first_air_date else None

    @property
    def seasons(self) -> list[int]:
        return sorted({e.season for e in self.episodes})


@dataclass(frozen=True, slots=True)
class SeriesCandidate:
    tmdb_id: int
    name: str
    original_name: str
    year: int | None
    overview: str


@dataclass(frozen=True, slots=True)
class MovieInfo:
    tmdb_id: int
    title: str
    original_title: str
    release_date: dt.date | None
    imdb_id: str | None
    runtime: int | None  # minutes
    fetched_at: int = 0

    @property
    def year(self) -> int | None:
        return self.release_date.year if self.release_date else None


def _date(value: Any) -> dt.date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def _retry_after(response: httpx.Response, default: float) -> float:
    """Seconds a 429 asks to wait (TMDB documents no header; honour it if one comes)."""
    value = response.headers.get("retry-after", "").strip()
    seconds = float(value) if value.replace(".", "", 1).isdigit() else default
    return min(max(seconds, 0.0), RETRY_AFTER_MAX)


def _episodes(season: int, data: dict[str, Any]) -> list[EpisodeInfo]:
    return [
        EpisodeInfo(
            season=int(e.get("season_number", season)),
            number=int(e["episode_number"]),
            name=str(e.get("name") or ""),
            air_date=_date(e.get("air_date")),
            runtime=e.get("runtime") if isinstance(e.get("runtime"), int) else None,
        )
        for e in data.get("episodes") or []
        if e.get("episode_number") is not None
    ]


def _series_to_json(info: SeriesInfo) -> str:
    return json.dumps(
        {
            "tmdb_id": info.tmdb_id,
            "name": info.name,
            "original_name": info.original_name,
            "first_air_date": info.first_air_date.isoformat() if info.first_air_date else None,
            "imdb_id": info.imdb_id,
            "tvdb_id": info.tvdb_id,
            "episodes": [
                [e.season, e.number, e.name, e.air_date.isoformat() if e.air_date else None, e.runtime]
                for e in info.episodes
            ],
        },
        ensure_ascii=False,
    )


def _series_from_json(text: str, fetched_at: int) -> SeriesInfo:
    data = json.loads(text)
    return SeriesInfo(
        tmdb_id=data["tmdb_id"],
        name=data["name"],
        original_name=data["original_name"],
        first_air_date=_date(data["first_air_date"]),
        imdb_id=data["imdb_id"],
        tvdb_id=data["tvdb_id"],
        episodes=tuple(EpisodeInfo(s, n, name, _date(day), runtime) for s, n, name, day, runtime in data["episodes"]),
        fetched_at=fetched_at,
    )


def _movie_to_json(info: MovieInfo) -> str:
    return json.dumps(
        {
            "tmdb_id": info.tmdb_id,
            "title": info.title,
            "original_title": info.original_title,
            "release_date": info.release_date.isoformat() if info.release_date else None,
            "imdb_id": info.imdb_id,
            "runtime": info.runtime,
        },
        ensure_ascii=False,
    )


def _movie_from_json(text: str, fetched_at: int) -> MovieInfo:
    data = json.loads(text)
    return MovieInfo(
        tmdb_id=data["tmdb_id"],
        title=data["title"],
        original_title=data["original_title"],
        release_date=_date(data["release_date"]),
        imdb_id=data["imdb_id"],
        runtime=data["runtime"],
        fetched_at=fetched_at,
    )


class MetadataCache:
    """TMDB answers in the state database: small, and back after a restart without asking TMDB again."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self, key: str) -> tuple[int, str] | None:
        row = self._db.one("SELECT fetched_at, value FROM metadata_cache WHERE key = ?", (key,))
        return None if row is None else (int(row[0]), str(row[1]))

    def put(self, key: str, value: str, fetched_at: int) -> None:
        self._db.run(
            "INSERT INTO metadata_cache (key, fetched_at, value) VALUES (?, ?, ?) ON CONFLICT (key) DO UPDATE"
            " SET fetched_at = excluded.fetched_at, value = excluded.value",
            (key, fetched_at, value),
        )


class Tmdb:
    def __init__(
        self,
        client: httpx.AsyncClient,
        token: Callable[[], str | None],
        cache: MetadataCache,
        *,
        language: str = LANGUAGE,
        max_age: int = MAX_AGE,
        rate: float = RATE,
        retry_delay: float = 2.0,
    ) -> None:
        self._client = client
        self._token = token
        self._cache = cache
        self._language = language
        self._max_age = max_age
        self._pace = Pace(rate)
        self._retry_delay = retry_delay
        self._locks: dict[int, asyncio.Lock] = {}

    @property
    def configured(self) -> bool:
        return bool((self._token() or "").strip())

    async def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        token = (self._token() or "").strip()
        if not token:
            raise MetadataError("no_token", "no TMDB access token configured")
        headers = {"Accept": "application/json"}
        query = dict(params or {})
        if _JWT.match(token):
            headers["Authorization"] = f"Bearer {token}"
        else:
            query["api_key"] = token
        for attempt in range(RETRIES + 1):
            await self._pace.wait()
            try:
                response = await self._client.get(f"{API}/{path}", params=query, headers=headers, timeout=TIMEOUT)
            except httpx.HTTPError as exc:  # the message would name the url, with a v3 key in it
                raise MetadataError("unavailable", f"TMDB not reachable ({type(exc).__name__})") from None
            if response.status_code != 429:
                break
            if attempt == RETRIES:
                raise MetadataError("unavailable", "TMDB rate limit reached, try again later")
            await asyncio.sleep(_retry_after(response, self._retry_delay * 2**attempt))
        if response.status_code == 401:
            raise MetadataError("unauthorized", "TMDB refused the access token")
        if response.status_code == 404:
            raise MetadataError("not_found", f"TMDB knows no {path.split('?')[0]}")
        if response.status_code != 200:
            raise MetadataError("unavailable", f"TMDB answered {response.status_code}")
        try:
            data = response.json()
        except ValueError:
            raise MetadataError("unavailable", "TMDB answered no JSON") from None
        if not isinstance(data, dict):
            raise MetadataError("unavailable", "TMDB answered no JSON object")
        return data

    async def test(self) -> None:
        """Raises MetadataError unless TMDB accepts the token."""
        await self._get("configuration")

    async def search(self, query: str) -> list[SeriesCandidate]:
        data = await self._get("search/tv", {"query": query, "language": self._language, "include_adult": "false"})
        out = []
        for r in data.get("results") or []:
            first = _date(r.get("first_air_date"))
            out.append(
                SeriesCandidate(
                    tmdb_id=int(r["id"]),
                    name=str(r.get("name") or ""),
                    original_name=str(r.get("original_name") or ""),
                    year=first.year if first else None,
                    overview=str(r.get("overview") or "")[:400],
                )
            )
        return out[:20]

    async def search_movies(self, query: str, year: int | None = None) -> list[SeriesCandidate]:
        """Movies on TMDB for a title (same shape as series candidates: id, title, original title, year)."""
        params = {"query": query, "language": self._language, "include_adult": "false"}
        if year:
            params["year"] = str(year)
        data = await self._get("search/movie", params)
        out = []
        for r in data.get("results") or []:
            released = _date(r.get("release_date"))
            out.append(
                SeriesCandidate(
                    tmdb_id=int(r["id"]),
                    name=str(r.get("title") or ""),
                    original_name=str(r.get("original_title") or ""),
                    year=released.year if released else None,
                    overview=str(r.get("overview") or "")[:400],
                )
            )
        return out[:20]

    async def movie(self, tmdb_id: int, *, max_age: int | None = None) -> MovieInfo:
        key = f"tmdb:movie:{tmdb_id}:{self._language}"
        limit = self._max_age if max_age is None else max_age
        cached = self._cache.get(key)
        if cached and time.time() - cached[0] < limit:
            return _movie_from_json(cached[1], cached[0])
        try:
            data = await self._get(f"movie/{tmdb_id}", {"language": self._language})
        except MetadataError as exc:
            if cached and exc.code == "unavailable":
                return _movie_from_json(cached[1], cached[0])
            raise
        info = MovieInfo(
            tmdb_id=int(data["id"]),
            title=str(data.get("title") or ""),
            original_title=str(data.get("original_title") or ""),
            release_date=_date(data.get("release_date")),
            imdb_id=data.get("imdb_id") or None,
            runtime=data.get("runtime") if isinstance(data.get("runtime"), int) else None,
            fetched_at=int(time.time()),
        )
        self._cache.put(key, _movie_to_json(info), info.fetched_at)
        return info

    async def series(self, tmdb_id: int, *, max_age: int | None = None) -> SeriesInfo:
        """The series with every episode, from the cache while younger than ``max_age`` seconds."""
        key = f"tmdb:tv:{tmdb_id}:{self._language}"
        limit = self._max_age if max_age is None else max_age
        cached = self._cache.get(key)
        if cached and time.time() - cached[0] < limit:
            return _series_from_json(cached[1], cached[0])
        async with self._locks.setdefault(tmdb_id, asyncio.Lock()):  # one fetch per series, however many ask
            cached = self._cache.get(key)
            if cached and time.time() - cached[0] < limit:
                return _series_from_json(cached[1], cached[0])
            known = sorted({e.season for e in _series_from_json(cached[1], cached[0]).episodes}) if cached else []
            try:
                info = await self._fetch(tmdb_id, known)
            except MetadataError as exc:
                if cached and exc.code == "unavailable":
                    log.warning("TMDB series %d: %s; using the answer from before", tmdb_id, exc)
                    return _series_from_json(cached[1], cached[0])
                raise
            self._cache.put(key, _series_to_json(info), info.fetched_at)
            return info

    async def _fetch(self, tmdb_id: int, known: list[int]) -> SeriesInfo:
        """The series with every season in as few requests as TMDB allows (``append_to_response``, ``APPEND``
        parts each). ``known``: the seasons of the answer before, asked for with the series, so a refresh costs one
        request (Die Anstalt: 1 instead of 15); a series new to the cache or a new season costs one more. A season a
        batch leaves out (listed, but empty on TMDB's side) is asked for on its own, 404 meaning no episodes."""
        params = {"language": self._language}
        first = ["external_ids", *(f"season/{n}" for n in known)][:APPEND]
        tv = await self._get(f"tv/{tmdb_id}", {**params, "append_to_response": ",".join(first)})
        numbers = [int(s["season_number"]) for s in tv.get("seasons") or [] if s.get("season_number") is not None]
        answers = {n: tv[f"season/{n}"] for n in numbers if isinstance(tv.get(f"season/{n}"), dict)}
        rest = [n for n in numbers if n not in answers and f"season/{n}" not in first]
        for start in range(0, len(rest), APPEND):
            batch = rest[start : start + APPEND]
            more = await self._get(
                f"tv/{tmdb_id}", {**params, "append_to_response": ",".join(f"season/{n}" for n in batch)}
            )
            answers |= {n: more[f"season/{n}"] for n in batch if isinstance(more.get(f"season/{n}"), dict)}
        for n in numbers:
            if n not in answers:
                try:
                    answers[n] = await self._get(f"tv/{tmdb_id}/season/{n}", params)
                except MetadataError as exc:
                    if exc.code != "not_found":
                        raise
                    answers[n] = {}
        seasons = [_episodes(n, answers[n]) for n in numbers]
        external = tv.get("external_ids") or {}
        tvdb = external.get("tvdb_id")
        return SeriesInfo(
            tmdb_id=int(tv["id"]),
            name=str(tv.get("name") or ""),
            original_name=str(tv.get("original_name") or ""),
            first_air_date=_date(tv.get("first_air_date")),
            imdb_id=external.get("imdb_id") or None,
            tvdb_id=int(tvdb) if isinstance(tvdb, int) else None,
            episodes=tuple(e for episodes in seasons for e in sorted(episodes, key=lambda e: e.number)),
            fetched_at=int(time.time()),
        )
