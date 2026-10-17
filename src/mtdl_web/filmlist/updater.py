"""One update round of the local index: choose full or diff, fetch, import, announce. A full list that was
downloaded but not imported (the container stopped during the import) is imported from disk first, not downloaded
again. The scheduler only calls ``update()``; it starts at a random minute per installation, not on the full hour."""

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
        self._from_disk: dt.datetime | None = None  # the full copy last tried from disk, by its fetched_at

    async def update(self) -> ImportResult | None:
        """Returns the import, or None when there was nothing new (304, list already imported, backing off).
        A failed fetch raises ``FetchError`` and moves the next attempt out."""
        async with self._lock:
            now = self._clock()
            if not self.backoff.ready(now):
                return None
            fetched, imported = self._fetcher.fetched_at("full"), self._store.last_full_at()
            try:
                if fetched is not None and fetched != self._from_disk and (imported is None or fetched > imported):
                    # We had a full list after the last import: the import did not finish, or the server still had
                    # a list we know (then there is nothing to do). From disk, before asking the server; once per
                    # copy, so a broken file cannot hold up the rounds until the next day's list replaces it.
                    self._from_disk = fetched
                    result = await self._import(self._fetcher.path("full"), "full")
                    if result is not None:
                        return result
                known = [t for t in (fetched, imported) if t is not None]
                return await self._update(plan(max(known, default=None), now), now)
            finally:
                self.progress = None

    async def _update(self, kind: Kind, now: dt.datetime) -> ImportResult | None:
        self.progress = Progress("download")
        try:
            fetched = await self._fetcher.fetch(kind, self._downloaded, now=now)
        except FetchError as exc:
            self.backoff.failed(now, exc.retry_after)
            raise
        self.backoff.succeeded()
        return await self._import(fetched.path, kind)

    async def _import(self, path: Path, kind: Kind) -> ImportResult | None:
        last = self._store.last_import()
        self.progress = Progress("import", total=last.total if last is not None and kind == "full" else None)
        result = await asyncio.to_thread(self._read, path, kind)
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

    def _read(self, path: Path, kind: Kind) -> ImportResult | None:
        with open_for_import(path, self._workers) as reader:
            if self._store.already_imported(reader.meta.list_id, kind):
                return None
            if kind == "full":
                return self._store.import_full(reader, progress=self._imported)
            return self._store.import_diff(reader, progress=self._imported)
