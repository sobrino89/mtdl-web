"""The running application: every long-lived component, created at start and closed at shutdown."""

import asyncio
import datetime as dt
import logging
import os
import random
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from mtdl_web.auth import Access
from mtdl_web.config import Settings
from mtdl_web.downloads.bandwidth import TokenBucket
from mtdl_web.downloads.manager import DownloadManager
from mtdl_web.downloads.queue import DownloadQueue
from mtdl_web.events import EventBus
from mtdl_web.filmlist.fetch import SERVER_DIRECTORY, ListFetcher
from mtdl_web.filmlist.store import FilmStore
from mtdl_web.filmlist.updater import FilmlistUpdater
from mtdl_web.hooks import HookRunner
from mtdl_web.http import make_client
from mtdl_web.libraries import LibraryStore
from mtdl_web.logos import LogoStore
from mtdl_web.metadata import MetadataCache, Tmdb
from mtdl_web.mounts import unmounted
from mtdl_web.plex import Plex, PlexRefresher
from mtdl_web.schedule import LimitState, limit_state
from mtdl_web.scheduler import FilmlistScheduler
from mtdl_web.sources import Source
from mtdl_web.sources.local import LocalSource
from mtdl_web.sources.mvw import MvwSource
from mtdl_web.state import AppSettings, StateStore
from mtdl_web.subscriptions import SubscriptionService, SubscriptionStore
from mtdl_web.webhooks import WebhookSender, WebhookStore

log = logging.getLogger(__name__)

RECHECK_EVERY = 60.0  # seconds between write tests while downloads wait for a folder


def write_test(directory: Path, *, create: bool = True, check_mount: bool = False) -> str | None:
    """None if ``directory`` is writable for this process, else the reason. Lesson from MediathekView (12.10.2026):
    Docker creates a missing bind-mount source as root, and every download fails later. ``create`` False: a missing
    folder is a problem, not something to make (a library inside the container would be lost, mounts.py)."""
    if not create and not directory.is_dir():
        return "missing (not mounted?)"
    if check_mount and unmounted(directory):
        return "not mounted: the folder lies inside the container, files there are lost with it"
    probe = directory / f".mtdl-write-test-{os.getpid()}"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as exc:
        return f"{type(exc).__name__}: {exc.strerror or exc}"
    return None


@dataclass
class Runtime:
    settings: Settings
    state: StateStore
    films: FilmStore
    bus: EventBus
    http: httpx.AsyncClient
    updater: FilmlistUpdater
    scheduler: FilmlistScheduler
    bucket: TokenBucket
    downloads: DownloadManager
    subscriptions: SubscriptionService
    webhooks: WebhookSender
    hooks: HookRunner
    logos: LogoStore
    tmdb: Tmdb
    plex: Plex
    plex_refresher: PlexRefresher
    libraries: LibraryStore
    access: Access
    # Results of the last write tests, by directory; a test writes a file, so it never runs per health check.
    writable: dict[str, str | None] = field(default_factory=dict)
    library_problems: dict[int, str | None] = field(default_factory=dict)  # by library id
    # The status bar switch paused the limit schedule until then (the end of the window); not kept over a restart.
    limit_paused_until: dt.datetime | None = None
    _tasks: list[asyncio.Task[None]] = field(default_factory=list)

    @classmethod
    def create(cls, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> "Runtime":
        for directory in (settings.config_dir, settings.cache_dir, settings.list_dir):
            directory.mkdir(parents=True, exist_ok=True)
        state = StateStore(settings.state_db)
        films = FilmStore(settings.film_db, index_description=settings.index_description)
        bus = EventBus()
        http = make_client(transport)
        fetcher = ListFetcher(http, settings.list_dir, server_directory=SERVER_DIRECTORY)
        updater = FilmlistUpdater(films, fetcher, bus, workers=settings.workers)
        minute = int(state.installation_value("filmlist_minute", lambda: random.randrange(60)))
        bucket = TokenBucket(limit_state(state.settings(), dt.datetime.now(settings.timezone)).rate)
        libraries = LibraryStore(state)
        libraries.adopt(settings.shows_dir, state.settings())  # the state of 0.1.0.dev16 to dev19, once
        state.mark_set_up_if_in_use()
        downloads = DownloadManager(
            DownloadQueue(state),
            state.settings,
            http,
            bucket,
            settings.download_dir,
            bus,
            ffmpeg_slots=settings.workers,
            libraries=libraries,
            check_mounts=settings.check_mounts,
        )
        tmdb = Tmdb(http, lambda: settings.tmdb_token or state.secret("tmdb_token"), MetadataCache(state.db))
        holder: list[Runtime] = []
        subscriptions = SubscriptionService(
            SubscriptionStore(state),
            downloads,
            lambda name: holder[0].source(name),
            state.settings,
            settings.download_dir,
            bus,
            settings.timezone,
            tmdb,
            libraries,
        )
        plex = Plex(
            http,
            lambda: state.settings().plex_url,
            lambda: settings.plex_token or state.secret("plex_token"),
            f"mtdl-web-{state.installation_id()}",
        )
        plex_refresher = PlexRefresher(plex, state.settings, libraries, bus)
        scheduler = FilmlistScheduler(updater, minute)
        webhooks = WebhookSender(WebhookStore(state), http, bus, download_dir=settings.download_dir)
        hooks = HookRunner(settings.hooks_dir, settings.download_dir, bus, settings.hook_timeout)
        hooks.prepare()
        logos = LogoStore(settings.cache_dir / "logos", http)
        access = Access(state, settings)
        access.reset_on_start()
        if access.needs_setup():  # the only place the code appears: whoever reads the log may choose the password
            log.warning("no password set yet: open mtdl-web and enter the setup code %s", access.new_setup_code())
        runtime = cls(
            settings,
            state,
            films,
            bus,
            http,
            updater,
            scheduler,
            bucket,
            downloads,
            subscriptions,
            webhooks,
            hooks,
            logos,
            tmdb,
            plex,
            plex_refresher,
            libraries,
            access,
        )
        holder.append(runtime)
        downloads.recheck = runtime.recheck_directories
        runtime.check_directories()
        return runtime

    def check_directories(self) -> dict[str, str | None]:
        guard = self.settings.check_mounts
        previous = dict(self.writable)
        self.writable = {
            "config": write_test(self.settings.config_dir, check_mount=guard),
            "downloads": write_test(self.settings.download_dir, check_mount=guard),
        }
        # Libraries are mounts: never made here (a folder inside the container would lose its files, mounts.py).
        self.library_problems = {
            library.id: write_test(library.path, create=False, check_mount=guard)
            for library in self.libraries.entries()
        }
        for library in self.libraries.entries():
            if self.library_problems[library.id]:
                self.writable[f"library {library.name}"] = self.library_problems[library.id]
        for name in {**previous, **self.writable}:  # only what changed: the test repeats while something fails
            problem, before = self.writable.get(name), previous.get(name)
            if problem and problem != before:
                log.error("%s directory is not writable: %s", name, problem)
            elif before and not problem:
                log.info("%s directory is writable again", name)
        blocked = [library_id for library_id, problem in self.library_problems.items() if problem]
        self.downloads.set_blocked(download_dir=bool(self.writable["downloads"]), libraries=blocked)
        return self.writable

    async def recheck_directories(self) -> None:
        await asyncio.to_thread(self.check_directories)

    async def _watch_directories(self) -> None:
        """While downloads wait for a folder that cannot be written (array stopped, share or mount missing), test it
        again every minute; they go on by themselves once it works. Nothing is tested while all is well: a write
        test would wake spun-down disks."""
        while True:
            await asyncio.sleep(RECHECK_EVERY)
            if self.downloads.blocked:
                try:
                    await self.recheck_directories()
                except Exception:
                    log.exception("folder check failed")

    def source(self, name: str | None = None) -> Source:
        chosen = name or self.state.settings().source
        if chosen == "mediathekviewweb":
            return MvwSource(self.http)
        return LocalSource(self.films)

    def start(self) -> None:
        self._tasks.append(asyncio.create_task(self.scheduler.run(), name="filmlist-scheduler"))
        self._tasks.append(asyncio.create_task(self.downloads.run(), name="download-dispatcher"))
        self._tasks.append(asyncio.create_task(self._limit_clock(), name="limit-schedule"))
        self._tasks.append(asyncio.create_task(self._watch_directories(), name="folder-watch"))
        self.hooks.start()

    def apply_settings(self, settings: AppSettings) -> None:
        """Changes in the UI act at once: bandwidth for running downloads, number of parallel downloads."""
        self.apply_limit(settings)
        self.downloads.wake()

    def limit_state(self, settings: AppSettings | None = None) -> LimitState:
        now = dt.datetime.now(self.settings.timezone)
        return limit_state(settings or self.state.settings(), now, self.limit_paused_until)

    def apply_limit(self, settings: AppSettings | None = None) -> LimitState:
        current = self.limit_state(settings)
        if current.source != "paused":
            self.limit_paused_until = None  # the window ended or the schedule is off: a pause is over
        if current.rate != self.bucket.rate:
            self.bucket.set_rate(current.rate)
        return current

    def pause_schedule(self, paused: bool) -> LimitState:
        """The status bar switch inside a schedule window: open until the window ends, or back to the schedule."""
        current = self.limit_state()
        if paused and current.source == "schedule":
            self.limit_paused_until = current.until
        elif not paused:
            self.limit_paused_until = None
        return self.apply_limit()

    async def _limit_clock(self) -> None:
        """Follows the schedule: windows begin and end without anyone touching a setting."""
        while True:
            await asyncio.sleep(30)
            try:
                self.apply_limit()
            except Exception:
                log.exception("limit schedule failed")

    async def close(self) -> None:
        # The dispatcher first: stopped downloads would otherwise free slots it fills with the next ones, which then
        # write into the closed database.
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.downloads.close()
        await self.webhooks.close()
        await self.hooks.close()
        await self.plex_refresher.close()
        await self.http.aclose()
        # An import still running in its thread keeps the film index; SQLite rolls the open transaction back on the
        # next start, so the process may simply end.
        if not self.films.importing:
            self.films.close()
        self.state.close()
