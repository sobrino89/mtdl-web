"""Background schedule for the film list: once at start, then hourly at a minute fixed per installation, so that
installations do not all ask the server at the full hour. Unchanged lists cost one 304 response."""

import asyncio
import contextlib
import datetime as dt
import logging
from collections.abc import Callable
from dataclasses import dataclass

from mtdl_web.filmlist.fetch import FetchError
from mtdl_web.filmlist.parser import FilmListError
from mtdl_web.filmlist.store import ImportRejected, ImportResult
from mtdl_web.filmlist.updater import FilmlistUpdater

log = logging.getLogger(__name__)


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def next_run(now: dt.datetime, minute: int) -> dt.datetime:
    """The next full hour plus ``minute``, strictly after ``now``."""
    candidate = now.replace(minute=minute, second=0, microsecond=0)
    return candidate if candidate > now else candidate + dt.timedelta(hours=1)


@dataclass(slots=True)
class FilmlistStatus:
    running: bool = False
    last_run: dt.datetime | None = None
    last_result: ImportResult | None = None
    last_error: str | None = None
    next_run: dt.datetime | None = None


class FilmlistScheduler:
    def __init__(
        self,
        updater: FilmlistUpdater,
        minute: int,
        *,
        clock: Callable[[], dt.datetime] = _utcnow,
    ) -> None:
        self._updater = updater
        self._minute = minute
        self._clock = clock
        self._wake = asyncio.Event()
        self.status = FilmlistStatus()

    def trigger(self) -> None:
        """Run an update now (UI button); the regular schedule continues afterwards."""
        self._wake.set()

    async def run_once(self) -> ImportResult | None:
        self.status.running = True
        self.status.last_run = self._clock()
        try:
            result = await self._updater.update()
        except (FetchError, FilmListError, ImportRejected, OSError) as exc:
            # Expected trouble: network, server, a broken list. Logged, shown in the UI, retried on schedule.
            self.status.last_error = f"{type(exc).__name__}: {exc}"
            log.warning("film list update failed: %s", self.status.last_error)
            return None
        finally:
            self.status.running = False
        self.status.last_error = None
        if result is not None:
            self.status.last_result = result
            log.info(
                "film list %s imported: +%d ~%d -%d of %d in %.1f s",
                result.kind,
                result.added,
                result.changed,
                result.removed,
                result.total,
                result.seconds,
            )
        return result

    async def run(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception as exc:  # a bug must not stop the schedule; it shows up in the log and the UI
                log.exception("film list update crashed")
                self.status.last_error = f"{type(exc).__name__}: {exc}"
            now = self._clock()
            due = next_run(now, self._minute)
            if self._updater.backoff.not_before is not None and self._updater.backoff.not_before > due:
                due = self._updater.backoff.not_before
            self.status.next_run = due
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=max(0.0, (due - now).total_seconds()))
