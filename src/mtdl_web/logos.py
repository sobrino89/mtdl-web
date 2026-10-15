"""Channel logos, optional (setting ``channel_logos``, off by default).

The project ships no logos: they are the broadcasters' trademarks. Switched on, the server fetches the SVGs of the
MediathekView desktop app from its repository (at a fixed commit, so nothing changes unnoticed), keeps them in the
cache folder and serves them itself; the browser loads nothing from elsewhere. Channels without a logo there (ORF,
SRF, DW) keep their text badge, and so does every channel while a fetch fails.
"""

import asyncio
import hashlib
import logging
import math
import time
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

SOURCE = (
    "https://raw.githubusercontent.com/mediathekview/MediathekView/"
    "55f8f7dfda8adaeb8f6d4112dac4b38e0d149639/src/main/resources/icons/sender/"
)
# Channel names of the film list (lower case) → file in SOURCE. All ARTE languages share one logo.
FILES = {
    "3sat": "3sat.svg",
    "ard": "ard.svg",
    "ard-alpha": "ard-alpha.svg",
    "br": "br.svg",
    "funk.net": "funk.svg",
    "hr": "hr.svg",
    "kika": "kika.svg",
    "mdr": "mdr.svg",
    "ndr": "ndr.svg",
    "one": "one.svg",
    "phoenix": "phoenix.svg",
    "radio bremen tv": "radio-bremen.svg",
    "rbtv": "radio-bremen.svg",
    "rbb": "rbb.svg",
    "sr": "sr.svg",
    "swr": "swr.svg",
    "tagesschau24": "tagesschau24.svg",
    "wdr": "wdr.svg",
    "zdf": "zdf.svg",
    "zdf-tivi": "ZDFtivi.svg",
    "zdfinfo": "ZDFinfo.svg",
    "zdfneo": "ZDFneo.svg",
}
MAX_BYTES = 200_000
RETRY_AFTER = 3600.0  # seconds before a failed fetch is tried again
TIMEOUT = httpx.Timeout(10.0)
# Logos a data source asks for next to its notice. TMDB: one of its approved logos (themoviedb.org/about/
# logos-attribution, "Alt short"); the file name is the SHA-256 of the file, checked after the download.
ATTRIBUTION = {
    "tmdb": "https://www.themoviedb.org/assets/v4/logos/v2/"
    "blue_short-8e7b30f73a4020692ccca9c88bafe5dcb6f8a62a4c6bc55cd9ba82bb2cd95f6c.svg",
}


def logo_file(channel: str) -> str | None:
    key = channel.strip().lower()
    return "arte.svg" if key.startswith("arte.") else FILES.get(key)


def _digest(url: str) -> str | None:
    """The SHA-256 a content-addressed file name carries ("…-<64 hex>.svg")."""
    stem = url.rsplit("/", 1)[-1].removesuffix(".svg")
    tail = stem.rsplit("-", 1)[-1]
    return tail if len(tail) == 64 and all(c in "0123456789abcdef" for c in tail) else None


def _is_svg(data: bytes) -> bool:
    return len(data) <= MAX_BYTES and b"<svg" in data[:4096]


class LogoStore:
    def __init__(self, directory: Path, client: httpx.AsyncClient) -> None:
        self.directory = directory
        self._client = client
        self._failed: dict[str, float] = {}  # file → time of the failed fetch
        self._locks: dict[str, asyncio.Lock] = {}

    async def get(self, channel: str) -> bytes | None:
        """The SVG for ``channel``, fetched once and then from the cache; None if there is none."""
        name = logo_file(channel)
        return None if name is None else await self._file(name, SOURCE + name)

    async def attribution(self, source: str) -> bytes | None:
        """The logo a data source asks for (TMDB), from its own server once, then from the cache."""
        url = ATTRIBUTION.get(source)
        return None if url is None else await self._file(f"attribution-{source}.svg", url)

    async def _file(self, name: str, url: str) -> bytes | None:
        path = self.directory / name
        if path.is_file():
            return path.read_bytes()
        if time.monotonic() - self._failed.get(name, -math.inf) < RETRY_AFTER:
            return None
        async with self._locks.setdefault(name, asyncio.Lock()):  # one fetch per file, however many ask
            if path.is_file():
                return path.read_bytes()
            try:
                response = await self._client.get(url, timeout=TIMEOUT)
                response.raise_for_status()
                data = response.content
            except httpx.HTTPError as exc:
                log.warning("logo %s not fetched: %s", name, exc)
                self._failed[name] = time.monotonic()
                return None
            expected = _digest(url)
            if not _is_svg(data) or (expected and hashlib.sha256(data).hexdigest() != expected):
                log.warning("logo %s is not the expected SVG (%d bytes), ignored", name, len(data))
                self._failed[name] = time.monotonic()
                return None
            self.directory.mkdir(parents=True, exist_ok=True)
            part = path.with_name(path.name + ".part")
            part.write_bytes(data)
            part.replace(path)
            return data
