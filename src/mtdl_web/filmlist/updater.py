"""One update round of the local index: choose full or diff, fetch, import, announce. The scheduler (next
session) only calls ``update()``; it should start at a random minute per installation, not on the full hour."""

import asyncio
import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from mtdl_web.events import EventBus, FilmlistUpdated
from mtdl_web.filmlist.fetch import Backoff, FetchError, ListFetcher, plan
from mtdl_web.filmlist.parallel import open_for_import
from mtdl_web.filmlist.store import FilmStore, ImportResult, Kind


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


@dataclass(slots=True)
class Progress:
    """The running update, for the progress bar of the first start: the download in bytes, then the import in
    films. ``total``: the size the server names, or the films of the last full list (none before the first)."""

    phase: Literal["download", "import"]
    done: int = 0
    total: int | None = None


class FilmlistUpdater:
    def __init__(
        self,
        store: FilmStore,
        fetcher: ListFetcher,
        bus: EventBus,
        *,
        clock: Callable[[], dt.datetime] = _utcnow,
        workers: int = 1,
    ) -> None:
        self._store = store
        self._fetcher = fetcher
        self._bus = bus
        self._clock = clock
        self._workers = workers
        self._lock = asyncio.Lock()
        self.backoff = Backoff()
        self.progress: Progress | None = None  # while an update runs

    async def update(self) -> ImportResult | None:
        """Returns the import, or None when there was nothing new (304, list already imported, backing off).
        A failed fetch raises ``FetchError`` and moves the next attempt out."""
        async with self._lock:
            now = self._clock()
            if not self.backoff.ready(now):
                return None
            kind, newer_than = plan(self._store.last_full_at(), now)
            try:
                return await self._update(kind, now, newer_than)
            finally:
                self.progress = None

    async def _update(self, kind: Kind, now: dt.datetime, newer_than: dt.datetime | None) -> ImportResult | None:
        self.progress = Progress("download")
        try:
            fetched = await self._fetcher.fetch(kind, self._downloaded, newer_than=newer_than)
        except FetchError as exc:
            self.backoff.failed(now, exc.retry_after)
            raise
        self.backoff.succeeded()
        last = self._store.last_import()
        self.progress = Progress("import", total=last.total if last is not None and kind == "full" else None)
        result = await asyncio.to_thread(self._import, fetched.path, kind)
        if result is not None:
            await self._bus.publish(
                FilmlistUpdated(
                    kind=result.kind,
                    list_id=result.list_id,
                    created=result.created,
                    added=result.added,
                    changed=result.changed,
                    removed=result.removed,
                    total=result.total,
                )
            )
        return result

    def _downloaded(self, done: int, total: int | None) -> None:
        if self.progress is not None:
            self.progress.done, self.progress.total = done, total

    def _imported(self, films: int) -> None:  # from the import thread: plain assignments only
        if self.progress is not None:
            self.progress.done = films

    def _import(self, path: Path, kind: Kind) -> ImportResult | None:
        with open_for_import(path, self._workers) as reader:
            if self._store.already_imported(reader.meta.list_id, kind):
                return None
            if kind == "full":
                return self._store.import_full(reader, progress=self._imported)
            return self._store.import_diff(reader, progress=self._imported)
