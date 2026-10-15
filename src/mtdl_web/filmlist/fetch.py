"""Polite download of the film list: full or diff by the Java client's rule, conditional requests, backoff.

Where the list lives says the project's server directory (``res.mediathekview.de/akt.xml`` and ``diff.xml``, the
way the Java client asks; forum.mediathekview.de, topic 3508, 17.06.2020): read once a day, the server with the
best ``Prio`` wins, and the official address stays the fallback. The team asked for no experiments with other
URLs and announced rate limiting; mtdl-web asks once an hour at a minute fixed per installation, revalidates with
ETag and Last-Modified, and backs off on errors, honouring Retry-After.
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

from mtdl_web.filmlist.store import ImportResult, Kind

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


def choose_kind(last: ImportResult | None, now: dt.datetime) -> Kind:
    """Rule of the Java client (``FilmListMetaData.canUseDiffList``): the server computes its diffs against a
    daily base list, so a diff only applies to a local list created after today's 07:00 UTC."""
    if last is None:
        return "full"
    base = dt.datetime.combine(now.astimezone(dt.UTC).date(), dt.time(7), dt.UTC)
    return "diff" if last.created > base else "full"


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

    async def fetch(self, kind: Kind, progress: Callable[[int, int | None], None] | None = None) -> Fetched:
        """``progress`` hears the bytes so far and the expected size (Content-Length, if the server names it)."""
        name = FILES[kind]
        url = await self.url(kind)
        target = self._dir / name
        part = self._dir / f"{name}.part"
        validators_file = self._dir / f"{name}.http.json"
        headers = {}
        if target.exists() and validators_file.exists():
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
