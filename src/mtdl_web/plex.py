"""Plex: read a library folder again after a download into it, and the connection test of the settings page.

Only needed when Plex does not notice new files by itself ("scan my library automatically" off, network shares
without change notifications), hence off by default. The token travels in a header (in the URL Plex would log
it); like every Plex client mtdl-web names itself with ``X-Plex-Product``, ``-Version`` and a stable
``-Client-Identifier`` (the installation id), plus the User-Agent of every request (http.py). It asks only on
demand: one scan request per finished download, two for a connection test.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

import httpx

from mtdl_web import __version__
from mtdl_web.events import DownloadCompleted, Event, EventBus
from mtdl_web.libraries import LibraryStore
from mtdl_web.state import AppSettings

log = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class PlexError(Exception):
    """``code``: no_url, unauthorized, unavailable, no_section."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True, slots=True)
class PlexSection:
    key: str
    title: str
    type: str  # show, movie, artist, photo
    paths: tuple[str, ...]


class Plex:
    def __init__(
        self,
        client: httpx.AsyncClient,
        url: Callable[[], str | None],
        token: Callable[[], str | None],
        identifier: str,
    ) -> None:
        self._client = client
        self._url = url
        self._token = token
        self._identifier = identifier

    async def _get(self, path: str, params: dict[str, str] | None = None) -> httpx.Response:
        base = (self._url() or "").rstrip("/")
        if not base:
            raise PlexError("no_url", "no Plex address configured")
        headers = {
            "Accept": "application/json",
            "X-Plex-Product": "mtdl-web",
            "X-Plex-Version": __version__,
            "X-Plex-Client-Identifier": self._identifier,
        }
        if token := (self._token() or "").strip():
            headers["X-Plex-Token"] = token
        try:
            response = await self._client.get(f"{base}{path}", params=params, headers=headers, timeout=TIMEOUT)
        except httpx.HTTPError as exc:
            raise PlexError("unavailable", f"Plex not reachable ({type(exc).__name__})") from None
        if response.status_code in (401, 403):
            raise PlexError("unauthorized", "Plex refused the request (token missing or wrong)")
        if response.status_code != 200:
            raise PlexError("unavailable", f"Plex answered {response.status_code}")
        return response

    async def _json(self, path: str) -> dict[str, Any]:
        try:
            data = (await self._get(path)).json()
        except ValueError:
            raise PlexError("unavailable", "Plex answered no JSON") from None
        container = data.get("MediaContainer") if isinstance(data, dict) else None
        return container if isinstance(container, dict) else {}

    async def version(self) -> str:
        return str((await self._json("/identity")).get("version") or "")

    async def sections(self) -> list[PlexSection]:
        return [
            PlexSection(
                key=str(d.get("key")),
                title=str(d.get("title") or ""),
                type=str(d.get("type") or ""),
                paths=tuple(str(loc.get("path")) for loc in d.get("Location") or [] if loc.get("path")),
            )
            for d in (await self._json("/library/sections")).get("Directory") or []
        ]

    async def refresh(self, folder: PurePosixPath) -> PlexSection:
        """Scans ``folder`` (as Plex sees it) in the section that contains it."""
        for section in await self.sections():
            if any(folder.is_relative_to(PurePosixPath(p)) for p in section.paths):
                await self._get(f"/library/sections/{section.key}/refresh", {"path": str(folder)})
                return section
        raise PlexError("no_section", f"no Plex library contains {folder}")


class PlexRefresher:
    """After a download into a library: Plex reads that folder again, if the library asks for it."""

    def __init__(self, plex: Plex, settings: Callable[[], AppSettings], libraries: LibraryStore, bus: EventBus):
        self._plex = plex
        self._settings = settings
        self._libraries = libraries
        self._tasks: set[asyncio.Task[None]] = set()
        bus.subscribe(self._on_event)

    def plex_folder(self, event: DownloadCompleted) -> PurePosixPath | None:
        """The folder of the downloaded file as Plex sees it; None unless its library wants a rescan."""
        library = self._libraries.get(event.library_id) if event.library_id is not None else None
        if library is None or not library.plex_refresh:
            return None
        return PurePosixPath(library.plex_path or library.path, PurePosixPath(event.path).parent)

    def _on_event(self, event: Event) -> None:
        if not isinstance(event, DownloadCompleted) or not self._settings().plex_url:
            return
        folder = self.plex_folder(event)
        if folder is None:
            return
        task = asyncio.create_task(self._refresh(folder), name="plex-refresh")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _refresh(self, folder: PurePosixPath) -> None:
        try:
            section = await self._plex.refresh(folder)
            log.info("Plex scans %s (library %s)", folder, section.title)
        except PlexError as exc:
            log.warning("Plex scan of %s failed: %s", folder, exc)

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
