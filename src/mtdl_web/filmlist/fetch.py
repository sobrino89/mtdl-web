"""Polite download of the film list: the full list once a day, otherwise the diff; conditional requests, backoff.

How the server works (MServer, ``Crawler.java``; forum.mediathekview.de, posts 4042, 11367 and 38221): from about
05:00 to 23:00 German time it writes a new list every hour, as a full list (~77 MB) and as a diff (~0.2 MB). The
diff holds everything that is new or changed since the day's first list (the "org" list) and never removes
anything; expired broadcasts only go with a full list. So a client needs one full list per day, after the day's
base exists, and diffs in between. The desktop client marks that point as 07:00 UTC (``canUseDiffList``) and loads
every 12 hours; mtdl-web asks every hour, like the project's Kodi add-on, which also loads the full list once a day
and diffs otherwise, at night too.

Where the list lives says the project's server directory (``res.mediathekview.de/akt.xml`` and ``diff.xml``;
forum topic 3508, 17.06.2020): read once a day, the server with the best ``Prio`` wins, and the official address
stays the fallback. The team asked for no experiments with other URLs and announced rate limiting; mtdl-web asks at
a minute fixed per installation, revalidates, and backs off on errors, honouring Retry-After.
"""

import datetime as dt
import email.utils
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

import httpx

from mtdl_web.filmlist.store import Kind

log = logging.getLogger(__name__)

BASE_URL = "https://liste.mediathekview.de/"
FILES: dict[Kind, str] = {"full": "Filmliste-akt.xz", "diff": "Filmliste-diff.xz"}
SERVER_DIRECTORY = "https://res.mediathekview.de/"
DIRECTORY_FILES: dict[Kind, str] = {"full": "akt.xml", "diff": "diff.xml"}
DIRECTORY_MAX_AGE = 24 * 3600.0
DIRECTORY_TIMEOUT = httpx.Timeout(15.0, connect=10.0)


def servers(xml: bytes) -> list[str]:
    """The list URLs of a server directory, best first (lowest ``Prio``); only https addresses."""
    entries = []
    for server in ElementTree.fromstring(xml).iter("Server"):
        url = (server.findtext("URL") or "").strip()
        prio = (server.findtext("Prio") or "").strip()
        if url.startswith("https://"):
            entries.append((int(prio) if prio.isdigit() else 99, url))
    return [url for _, url in sorted(entries)]


DAY_STARTS = dt.time(7)  # UTC: by then the day's base list exists (the desktop client's mark)


def plan(last_full: dt.datetime | None, now: dt.datetime) -> tuple[Kind, dt.datetime | None]:
    """What to fetch, and for a full list the time it must be newer than (else the server answers 304).

    ``last_full``: when the newest full list was imported. Once a day, from 07:00 UTC, the full list is due: the
    request asks only for a list published after 07:00, so the next hours bring nothing until the server has one,
    and then it comes exactly once. Between full lists, also at night, the diff: night diffs already refer to the
    next day's base, but they add what is new; the day's full list fills the rest. A list older than the last
    07:00 (the first start, a machine that was off) is replaced at once."""
    day = now.astimezone(dt.UTC)
    start = dt.datetime.combine(day.date(), DAY_STARTS, dt.UTC)
    if start > day:
        start -= dt.timedelta(days=1)
    if last_full is not None and last_full >= start:
        return "diff", None
    return "full", start


class FetchError(Exception):
    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class Fetched:
    path: Path
    modified: bool  # False: the server answered 304, ``path`` is the file from the previous download


def _retry_after(response: httpx.Response, now: dt.datetime) -> float | None:
    value = response.headers.get("retry-after")
    if not value:
        return None
    if value.isdigit():
        return float(value)
    try:
        return max(0.0, (email.utils.parsedate_to_datetime(value) - now).total_seconds())
    except (TypeError, ValueError):
        return None


class ListFetcher:
    """Downloads into ``directory``; the validators (ETag, Last-Modified) live next to each file. With
    ``server_directory`` the URL comes from the project's directory, else straight from ``base_url``."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        directory: Path,
        base_url: str = BASE_URL,
        server_directory: str | None = None,
    ) -> None:
        self._client = client
        self._dir = directory
        self._base_url = base_url
        self._server_directory = server_directory
        self._known: dict[Kind, tuple[float, str]] = {}

    async def url(self, kind: Kind) -> str:
        fallback = self._base_url + FILES[kind]
        if self._server_directory is None:
            return fallback
        known = self._known.get(kind)
        if known is not None and time.monotonic() - known[0] < DIRECTORY_MAX_AGE:
            return known[1]
        found: list[str] = []
        try:
            response = await self._client.get(self._server_directory + DIRECTORY_FILES[kind], timeout=DIRECTORY_TIMEOUT)
            response.raise_for_status()
            found = servers(response.content)
        except (httpx.HTTPError, ElementTree.ParseError) as exc:
            log.warning("film list server directory not readable (%s), using %s", exc, fallback)
        url = found[0] if found else fallback
        self._known[kind] = (time.monotonic(), url)
        return url

    async def fetch(
        self,
        kind: Kind,
        progress: Callable[[int, int | None], None] | None = None,
        *,
        newer_than: dt.datetime | None = None,
    ) -> Fetched:
        """``progress`` hears the bytes so far and the expected size (Content-Length, if the server names it).
        ``newer_than``: only a list published after this time (the day's full list, ``plan``); until the server has
        one it answers 304. Otherwise the copy we have is revalidated with ETag and Last-Modified. Behind the name
        are two servers whose ETags differ for the same file, so the full list asks by time alone."""
        name = FILES[kind]
        url = await self.url(kind)
        target = self._dir / name
        part = self._dir / f"{name}.part"
        validators_file = self._dir / f"{name}.http.json"
        headers = {}
        if target.exists() and newer_than is not None:
            headers["If-Modified-Since"] = email.utils.format_datetime(newer_than.astimezone(dt.UTC), usegmt=True)
        elif target.exists() and validators_file.exists():
            validators = json.loads(validators_file.read_text())
            if validators.get("etag"):
                headers["If-None-Match"] = validators["etag"]
            if validators.get("last_modified"):
                headers["If-Modified-Since"] = validators["last_modified"]
        self._dir.mkdir(parents=True, exist_ok=True)
        try:
            async with self._client.stream("GET", url, headers=headers) as response:
                if response.status_code == httpx.codes.NOT_MODIFIED:
                    return Fetched(target, modified=False)
                if response.status_code != httpx.codes.OK:
                    retry_after = _retry_after(response, dt.datetime.now(dt.UTC))
                    raise FetchError(f"{name}: HTTP {response.status_code}", retry_after=retry_after)
                length = response.headers.get("content-length", "")
                expected = int(length) if length.isdigit() else None
                done = 0
                with part.open("wb") as out:
                    async for chunk in response.aiter_bytes(1 << 16):
                        out.write(chunk)
                        done += len(chunk)
                        if progress is not None:
                            progress(done, expected)
                validators = {
                    "etag": response.headers.get("etag"),
                    "last_modified": response.headers.get("last-modified"),
                }
        except httpx.HTTPError as exc:
            raise FetchError(f"{name}: {exc!r}") from exc
        part.replace(target)
        validators_file.write_text(json.dumps(validators))
        return Fetched(target, modified=True)


@dataclass(slots=True)
class Backoff:
    """Exponential backoff after failed fetches; a server's Retry-After wins when it asks for longer."""

    base: float = 60.0
    maximum: float = 3600.0
    failures: int = 0
    not_before: dt.datetime | None = None

    def ready(self, now: dt.datetime) -> bool:
        return self.not_before is None or now >= self.not_before

    def failed(self, now: dt.datetime, retry_after: float | None = None) -> dt.datetime:
        self.failures += 1
        delay = min(self.maximum, self.base * 2 ** (self.failures - 1))
        if retry_after is not None:
            delay = max(delay, retry_after)
        self.not_before = now + dt.timedelta(seconds=delay)
        return self.not_before

    def succeeded(self) -> None:
        self.failures = 0
        self.not_before = None
