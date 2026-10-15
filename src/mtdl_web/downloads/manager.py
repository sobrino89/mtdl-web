"""Runs the download queue: as many at once as the setting allows (changeable at runtime), each byte through the
shared bandwidth limit, ffmpeg runs limited by MTDL_WORKERS. Downloads continue after a restart."""

import asyncio
import contextlib
import errno
import logging
import os
import shutil
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

import httpx

from mtdl_web.downloads import DownloadError
from mtdl_web.downloads.bandwidth import TokenBucket
from mtdl_web.downloads.hls import MasterPlaylist, MediaPlaylist, choose, download_media, fetch_playlist, mux, parse
from mtdl_web.downloads.queue import ACTIVE, Download, DownloadQueue, State
from mtdl_web.downloads.subtitles import convert
from mtdl_web.downloads.transfer import fetch, fetch_bytes
from mtdl_web.episodes import episode_key, same_duration, same_file, videos
from mtdl_web.events import DownloadCompleted, DownloadEvent, DownloadFailed, DownloadQueued, DownloadStarted, EventBus
from mtdl_web.libraries import LibraryStore
from mtdl_web.library import IGNORE_FILES, STAGING
from mtdl_web.models import Film
from mtdl_web.mounts import unmounted
from mtdl_web.state import AppSettings

log = logging.getLogger(__name__)

RETRIES = 3  # attempts in all for temporary errors: two automatic retries, 5 and 10 minutes later
RETRY_DELAY = 300
PROGRESS_EVERY = 5.0  # seconds between progress writes to the database


class Duplicate(Exception):
    """The broadcast is already downloaded, queued or running."""

    def __init__(self, existing: Download) -> None:
        super().__init__(f"already {existing.state}: download {existing.id}")
        self.existing = existing


def error_code(exc: BaseException, film: Film) -> str:
    """The kind of a failure; the UI explains each kind and what helps. Codes: geo, forbidden, gone, http, network,
    permission, disk_full, filesystem, not_mounted, mux, format, livestream, no_url, unknown."""
    if isinstance(exc, DownloadError):
        if exc.status in (401, 403, 451):
            return "geo" if film.geo else "forbidden"
        if exc.status in (404, 410):
            return "gone"
        if exc.status is not None:
            return "http"
        return exc.code or "unknown"
    if isinstance(exc, PermissionError):
        return "permission"
    if isinstance(exc, OSError):
        return "disk_full" if exc.errno in (errno.ENOSPC, errno.EDQUOT) else "filesystem"
    return "unknown"


def pick_url(film: Film, quality: str) -> str | None:
    """The wanted quality, else the next lower, else the next higher one."""
    order = {"hd": (film.url_hd, film.url, film.url_low), "normal": (film.url, film.url_low, film.url_hd)}
    candidates = order.get(quality, (film.url_low, film.url, film.url_hd))
    return next((u for u in candidates if u), None)


def is_hls(url: str) -> bool:
    return urlsplit(url).path.lower().endswith(".m3u8")


def is_livestream(film: Film) -> bool:
    return film.topic == "Livestream" or (film.timestamp is None and film.duration is None)


@dataclass
class Live:
    """In-memory progress of a running download, for the API."""

    done: int = 0
    total: int | None = None
    speed: float = 0.0  # byte/s, smoothed
    phase: str = "download"  # download, mux, subtitles
    _last_bytes: int = 0
    _last_time: float = 0.0
    _saved: float = 0.0


class DownloadManager:
    def __init__(
        self,
        queue: DownloadQueue,
        settings: Callable[[], AppSettings],
        client: httpx.AsyncClient,
        bucket: TokenBucket,
        download_dir: Path,
        bus: EventBus,
        *,
        ffmpeg_slots: int = 1,
        ffmpeg: str = "ffmpeg",
        retry_delay: int = RETRY_DELAY,
        transfer_retry_delay: float = 2.0,
        libraries: LibraryStore | None = None,
        check_mounts: bool = False,
    ) -> None:
        self.queue = queue
        self._settings = settings
        self._client = client
        self._bucket = bucket
        self._dir = download_dir
        self._libraries = libraries
        self._check_mounts = check_mounts
        self._bus = bus
        self._ffmpeg = ffmpeg
        self._ffmpeg_slots = asyncio.Semaphore(ffmpeg_slots)
        self._retry_delay = retry_delay
        self._transfer_retry_delay = transfer_retry_delay
        self._wake = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: dict[int, asyncio.Task[None]] = {}
        self._user_cancelled: set[int] = set()
        self.live: dict[int, Live] = {}
        # Folders that cannot be written (the runtime's write test): downloads into them wait instead of failing.
        self._blocked_dir = False
        self._blocked_libraries: frozenset[int] = frozenset()
        self.recheck: Callable[[], Awaitable[None]] | None = None  # the runtime: test the folders again

    # -- queueing -------------------------------------------------------------------------------------------------

    def find_existing(self, film: Film, states: tuple[State, ...] = ACTIVE) -> Download | None:
        """A download of the same broadcast in one of ``states`` (default: running, waiting or done), the first state
        in ``states`` winning: same key and duration, or the same file on the same day (3sat's copy of ZDF's
        "Die Anstalt" under another title)."""
        for download in self.queue.by_episode(episode_key(film), states):
            if same_duration(download.film.duration, film.duration):
                return download
        return next((d for d in self.queue.by_video(videos(film), states) if same_file(d.film, film)), None)

    async def enqueue(
        self,
        film: Film,
        path: PurePosixPath,
        *,
        quality: str | None = None,
        subtitles: bool | None = None,
        subscription_id: int | None = None,
        library_id: int | None = None,
        force: bool = False,
    ) -> Download:
        """``path`` is relative to the library ``library_id``, else to the download folder."""
        if is_livestream(film):
            raise DownloadError("livestream: cannot be downloaded", code="livestream")
        if not force and (existing := self.find_existing(film)):
            raise Duplicate(existing)
        settings = self._settings()
        download = self.queue.add(
            film,
            path=str(path),
            quality=quality or settings.quality,
            subtitles=settings.subtitles if subtitles is None else subtitles,
            episode_key=episode_key(film),
            subscription_id=subscription_id,
            library_id=library_id,
        )
        await self._bus.publish(DownloadQueued(**self._event_fields(download)))
        self.wake()
        return download

    def wake(self) -> None:
        """Also from a worker thread (a sync API route, the folder test): then through the event loop."""
        try:
            current: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if self._loop is not None and current is not self._loop:
            self._loop.call_soon_threadsafe(self._wake.set)
        else:
            self._wake.set()

    @property
    def blocked(self) -> bool:
        return self._blocked_dir or bool(self._blocked_libraries)

    def set_blocked(self, *, download_dir: bool, libraries: Iterable[int]) -> None:
        """The folders that failed the last write test; a folder that works again lets its downloads go on."""
        libraries = frozenset(libraries)
        freed = (self._blocked_dir and not download_dir) or bool(self._blocked_libraries - libraries)
        self._blocked_dir, self._blocked_libraries = download_dir, libraries
        if freed:
            self.wake()

    async def cancel(self, download_id: int) -> bool:
        if not self.queue.cancel(download_id):
            return False
        task = self._tasks.get(download_id)
        if task is not None:
            self._user_cancelled.add(download_id)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        download = self.queue.get(download_id)
        if download is not None:
            self._remove_partials(download)
        self.wake()
        return True

    async def cancel_all(self, subscription_id: int | None = None) -> list[int]:
        """Everything waiting or loading, or a subscription's part of it; the waiting ones first, so that none of
        them starts in the slot a cancelled one frees. Returns the ids cancelled (to undo with retry)."""
        waiting = self.queue.entries(("queued",), limit=100_000, order="queue", subscription_id=subscription_id)
        running = self.queue.entries(("running",), limit=100_000, subscription_id=subscription_id)
        return [d.id for d in [*waiting, *running] if await self.cancel(d.id)]

    def retry(self, download_id: int) -> bool:
        ok = self.queue.retry(download_id)
        self.wake()
        return ok

    def move(self, download_id: int, index: int) -> bool:
        return self.queue.move(download_id, index)

    # -- running --------------------------------------------------------------------------------------------------

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.queue.requeue_running()
        self._tidy_staging()  # left over by an older version or a crash; downloads that resume keep their folder
        while True:
            limit = self._settings().parallel_downloads
            while len(self._tasks) < limit and (download := self._claim()) is not None:
                task = asyncio.create_task(self._run_one(download), name=f"download-{download.id}")
                self._tasks[download.id] = task
            self._wake.clear()
            timeout = None
            if (due := self.queue.next_wakeup()) is not None:
                timeout = max(1.0, due - time.time())
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=timeout)

    def _claim(self) -> Download | None:
        return self.queue.claim_next(skip_download_dir=self._blocked_dir, skip_libraries=self._blocked_libraries)

    def _folder_blocked(self, download: Download) -> bool:
        if download.library_id is None:
            return self._blocked_dir
        return download.library_id in self._blocked_libraries

    async def _waits_for_folder(self, download: Download, error: str, code: str) -> bool:
        """After a file system error: does the whole folder (download folder or library) fail its write test? Then
        the download waits for it instead of failing for good (Unraid: array stopped, share or mount missing). An
        error with one file in a folder that works stays a failure."""
        if self.recheck is None:
            return False
        await self.recheck()
        if not self._folder_blocked(download):
            return False
        self.queue.wait(download.id, error, code=code)
        log.warning("download %d waits until its folder can be written again: %s", download.id, error)
        return True

    async def close(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_one(self, download: Download) -> None:
        live = self.live.setdefault(download.id, Live(_last_time=time.monotonic()))
        try:
            await self._bus.publish(DownloadStarted(**self._event_fields(download)))
            size, subtitle = await self._download(download, live)
            self.queue.finish(download.id, size)
            fields = self._event_fields(download)
            subtitle_file = str(Path(fields["file"]).with_name(PurePosixPath(subtitle).name)) if subtitle else None
            await self._bus.publish(
                DownloadCompleted(**fields, size=size, subtitle_path=subtitle, subtitle_file=subtitle_file)
            )
        except asyncio.CancelledError:
            if download.id not in self._user_cancelled:
                raise  # shutdown: the entry stays "running" and resumes after the restart
        except DownloadError as exc:
            code = error_code(exc, download.film)
            if not (code == "not_mounted" and await self._waits_for_folder(download, str(exc), code)):
                await self._failed(download, str(exc), code, retryable=exc.retryable)
        except OSError as exc:  # a folder that cannot be written waits; disk full and single files fail for good
            error, code = f"{type(exc).__name__}: {exc}", error_code(exc, download.film)
            if not (code in ("permission", "filesystem") and await self._waits_for_folder(download, error, code)):
                await self._failed(download, error, code, retryable=False)
        except Exception as exc:
            log.exception("download %d crashed", download.id)
            await self._failed(download, f"{type(exc).__name__}: {exc}", "unknown", retryable=False)
        finally:
            self._tasks.pop(download.id, None)
            self._user_cancelled.discard(download.id)
            self.live.pop(download.id, None)
            self.wake()

    async def _failed(self, download: Download, error: str, code: str, *, retryable: bool) -> None:
        if code == "geo":
            error += f" (geo restriction: {'-'.join(download.film.geo)})"
        will_retry = retryable and download.attempts < RETRIES
        retry_at = int(time.time()) + self._retry_delay * 2 ** (download.attempts - 1) if will_retry else None
        self.queue.fail(download.id, error, code=code, retry_at=retry_at)
        log.warning("download %d failed%s: %s", download.id, " (will retry)" if will_retry else "", error)
        await self._bus.publish(DownloadFailed(**self._event_fields(download), error=error, will_retry=will_retry))

    def _progress(self, download_id: int, live: Live, done: int, total: int | None) -> None:
        now = time.monotonic()
        live.done, live.total = done, total
        elapsed = now - live._last_time
        if elapsed >= 1.0:
            current = (done - live._last_bytes) / elapsed
            live.speed = current if live.speed == 0 else 0.7 * live.speed + 0.3 * current
            live._last_bytes, live._last_time = done, now
        if now - live._saved >= PROGRESS_EVERY:
            live._saved = now
            self.queue.progress(download_id, done, total)

    def file_of(self, download: Download) -> str:
        """The video as this process sees it, absolute (a download's path is relative to its library)."""
        target, _ = self._work(download)
        return str(target)

    def _event_fields(self, download: Download) -> dict[str, Any]:
        film = download.film
        return {
            "download_id": download.id,
            "film_id": str(film.id),
            "channel": film.channel,
            "topic": film.topic,
            "title": film.title,
            "path": download.path,
            "subscription_id": download.subscription_id,
            "file": self.file_of(download),
            "library_id": download.library_id,
        }

    def _root(self, download: Download) -> Path | None:
        """The folder the download's path is relative to: its library, the download folder without one; None when
        its library is gone."""
        if download.library_id is None:
            return self._dir
        library = self._libraries.get(download.library_id) if self._libraries is not None else None
        return library.path if library is not None else None

    def _work(self, download: Download) -> tuple[Path, Path]:
        """The target file and the folder for its unfinished parts: next to it in the download folder; in a library
        a hidden staging folder inside it, so the media server never sees half a file or HLS segments, and the
        finished file still arrives by an atomic rename on the same file system."""
        root = self._root(download)
        target = (root if root is not None else self._dir) / download.path
        if download.library_id is not None and root is not None:
            return target, root / STAGING / str(download.id)
        return target, target.parent

    def _root_problem(self, download: Download) -> str | None:
        """Why nothing may be written for ``download``: its library is gone, its folder missing or not mounted, or
        its path leads out of it (an absolute path of an old version). Written anyway, the file would lie inside the
        container and vanish with it (mounts.py)."""
        root = self._root(download)
        if root is None:
            return f"library {download.library_id} no longer exists"
        if download.library_id is not None and not root.is_dir():
            return f"{root} is missing (not mounted?)"
        if not Path(os.path.normpath(root / download.path)).is_relative_to(root):  # "..", or absolute
            return f"{(root / download.path).parent} lies outside {root}"
        if self._check_mounts and unmounted(root):
            return f"{root} is not mounted: files there would stay inside the container and be lost with it"
        return None

    def _stage(self, work: Path) -> None:
        staging = work.parent
        staging.mkdir(parents=True, exist_ok=True)
        for name in IGNORE_FILES:
            ignore = staging / name
            if not ignore.exists():
                ignore.write_text("# mtdl-web: unfinished downloads\n*\n", encoding="utf-8")
        work.mkdir(exist_ok=True)

    async def _download(self, download: Download, live: Live) -> tuple[int, str | None]:
        film = download.film
        target, work = self._work(download)
        if problem := self._root_problem(download):
            raise DownloadError(problem, code="not_mounted")
        if target.exists():
            size = target.stat().st_size
            log.info("download %d: %s exists already, nothing to load", download.id, download.path)
            return size, None
        url = pick_url(film, download.quality)
        if url is None:
            raise DownloadError("no usable video url in the film list entry", code="no_url")
        # In a library the target folder appears with the finished file: a cancelled download leaves no empty one.
        staged = work != target.parent
        if staged:
            self._stage(work)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
        part = work / (target.name + ".part")

        def progress(done: int, total: int | None) -> None:
            self._progress(download.id, live, done, total)

        if is_hls(url):
            await self._download_hls(url, download, work / (target.name + ".parts"), part, live, progress)
        else:
            await fetch(self._client, url, part, self._bucket, progress, retry_delay=self._transfer_retry_delay)
        target.parent.mkdir(parents=True, exist_ok=True)
        part.replace(target)
        if staged:
            shutil.rmtree(work, ignore_errors=True)
            self._tidy_staging()
        size = target.stat().st_size
        subtitle = await self._subtitles(download, target, live) if download.subtitles else None
        return size, subtitle

    async def _download_hls(
        self,
        url: str,
        download: Download,
        work: Path,
        part: Path,
        live: Live,
        progress: Callable[[int, int | None], None],
    ) -> None:
        text, final_url = await fetch_playlist(self._client, url, self._bucket)
        playlist = parse(text, final_url)
        audio_url = None
        if isinstance(playlist, MasterPlaylist):
            variant, audio = choose(playlist, download.quality)  # type: ignore[arg-type]
            text, final_url = await fetch_playlist(self._client, variant.uri, self._bucket)
            playlist = parse(text, final_url)
            audio_url = audio.uri if audio else None
        if not isinstance(playlist, MediaPlaylist):
            raise DownloadError("HLS variant is not a media playlist", code="format")

        def segments(done: int, total: int, received: int) -> None:
            estimate = int(received / done * total) if done else None
            progress(received, estimate)

        video = await download_media(self._client, playlist, work / "video", self._bucket, segments)
        audio_local = None
        if audio_url:
            text, final_url = await fetch_playlist(self._client, audio_url, self._bucket)
            audio_playlist = parse(text, final_url)
            if not isinstance(audio_playlist, MediaPlaylist):
                raise DownloadError("HLS audio rendition is not a media playlist", code="format")
            audio_local = await download_media(self._client, audio_playlist, work / "audio", self._bucket, segments)
        live.phase = "mux"
        async with self._ffmpeg_slots:
            await mux(video, audio_local, part, ffmpeg=self._ffmpeg)
        shutil.rmtree(work, ignore_errors=True)

    async def _subtitles(self, download: Download, target: Path, live: Live) -> str | None:
        """Subtitles are a bonus: a failure here never fails the download."""
        film = download.film
        if not film.url_subtitle:
            return None
        live.phase = "subtitles"
        try:
            data = await fetch_bytes(self._client, film.url_subtitle, self._bucket, retries=1, retry_delay=1.0)
        except DownloadError as exc:
            log.info("download %d: no subtitles (%s)", download.id, exc)
            return None
        srt = convert(data, film.duration)
        if srt is None:
            log.info("download %d: subtitle format not recognised: %s", download.id, film.url_subtitle)
            return None
        path = target.with_name(target.stem + ".de.srt")
        path.write_text(srt, encoding="utf-8")
        return str(PurePosixPath(download.path).with_name(path.name))

    def _tidy_staging(self) -> None:
        """A library's staging folder exists only while something loads into it: empty but
        for its ignore files, it goes with them, and the next download into the library makes them again. No await
        lies between this check and a download's own staging (_stage), so no other download can slip in between."""
        if self._libraries is None:
            return
        for library in self._libraries.entries():
            staging = library.path / STAGING
            try:
                if staging.is_dir() and {p.name for p in staging.iterdir()} <= set(IGNORE_FILES):
                    for name in IGNORE_FILES:
                        (staging / name).unlink(missing_ok=True)
                    staging.rmdir()
            except OSError:
                pass  # gone already, or something arrived meanwhile: leave it

    def _remove_partials(self, download: Download) -> None:
        """Best effort: a folder that cannot be written (why the download waited, perhaps) keeps its parts, and the
        cancel still counts."""
        target, work = self._work(download)
        try:
            if work != target.parent:
                shutil.rmtree(work, ignore_errors=True)
                self._tidy_staging()
                return
            with contextlib.suppress(FileNotFoundError):
                (work / (target.name + ".part")).unlink()
            shutil.rmtree(work / (target.name + ".parts"), ignore_errors=True)
        except OSError as exc:
            log.warning("download %d: partial files stay (%s)", download.id, exc)


__all__ = ["DownloadEvent", "DownloadManager", "Duplicate", "error_code", "is_hls", "is_livestream", "pick_url"]
