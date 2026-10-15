"""Hook scripts: own programs per event for post-processing, e.g. move, convert or tag a download.

Every executable file in ``<config>/hooks/<event>.d/`` runs for that event, in name order. Scripts run one at a time
and events in the order they happened, so a script may rely on the one before. Input: the event as JSON on stdin (the
webhook default body) and as environment variables: ``MTDL_EVENT``, ``MTDL_TIME``, ``MTDL_<FIELD>`` for every field
(``MTDL_TITLE``, ``MTDL_PATH`` …) and for downloads ``MTDL_FILE`` and ``MTDL_SUBTITLE_FILE`` with absolute paths.
The working directory is the download folder; a script runs as the app's user with its umask and sees the
container's environment. After the timeout its process group gets SIGTERM, a few seconds later SIGKILL. Output and
exit code go to the log, the last run of every script also to the settings. The exit code changes nothing else.

Skipped: files without the executable bit (``chmod -x`` switches a script off), names starting with a dot or ending
with ``~``. Scripts come only from the config folder: the API lists them, it never writes or starts one.
"""

import asyncio
import contextlib
import dataclasses
import datetime as dt
import json
import logging
import os
import signal
import stat
import time
from pathlib import Path
from typing import Any

from mtdl_web.events import Event, EventBus
from mtdl_web.webhooks import EVENTS, payload

log = logging.getLogger(__name__)

OUTPUT_TAIL = 8192  # bytes of output kept per run
LOG_LINES = 20  # of them in the log


@dataclasses.dataclass(frozen=True, slots=True)
class Run:
    event: str
    started_at: dt.datetime
    seconds: float
    exit_code: int | None  # None: did not start, or stopped after the timeout (see error)
    output: str  # the end of stdout and stderr
    error: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class Script:
    event: str
    name: str
    path: Path
    executable: bool


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


class _Tail:
    """The last ``OUTPUT_TAIL`` bytes of a stream."""

    def __init__(self) -> None:
        self.data = bytearray()
        self.total = 0

    async def read(self, stream: asyncio.StreamReader) -> None:
        while chunk := await stream.read(65536):
            self.total += len(chunk)
            self.data += chunk
            del self.data[:-OUTPUT_TAIL]

    def text(self) -> str:
        text = self.data.decode(errors="replace")
        if self.total > len(self.data):  # cut in the middle of a line: start with the next one
            text = "…\n" + text.partition("\n")[2]
        return text.rstrip("\n")


class HookRunner:
    def __init__(
        self, directory: Path, download_dir: Path, bus: EventBus, timeout: float = 600, grace: float = 5
    ) -> None:
        self.directory = directory
        self.timeout = timeout
        self._grace = grace
        self._download_dir = download_dir
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._running: asyncio.subprocess.Process | None = None
        self._draining: set[asyncio.Task[None]] = set()  # output of children that outlive their script
        self.last: dict[tuple[str, str], Run] = {}  # by (event, name)
        bus.subscribe(self._on_event)

    def prepare(self) -> None:
        """A folder per event, so users find where scripts go. Not writable for group or others, whatever the umask:
        who can put a file there runs code in the container. Folders that are (an older version, UMASK=000) lose that
        right; for folders of another owner a warning says so."""
        for folder in (self.directory, *(self.directory / f"{event}.d" for event in EVENTS)):
            try:
                folder.mkdir(mode=0o755, parents=True, exist_ok=True)
                mode = stat.S_IMODE(folder.stat().st_mode)
                if mode & 0o022:
                    folder.chmod(mode & ~0o022)
                    log.warning("hook folder %s was writable for group or others, now %o", folder, mode & ~0o022)
            except OSError as exc:
                log.warning("hook folder %s: %s", folder, exc)

    def scripts(self, event: str | None = None) -> list[Script]:
        found = []
        for name in EVENTS if event is None else (event,):
            try:
                paths = sorted((self.directory / f"{name}.d").iterdir())
            except OSError:
                continue  # no folder, no scripts
            for path in paths:
                if path.name.startswith(".") or path.name.endswith("~") or not path.is_file():
                    continue
                found.append(Script(name, path.name, path, os.access(path, os.X_OK)))
        return found

    def environment(self, body: dict[str, Any]) -> dict[str, str]:
        data: dict[str, Any] = body["data"]
        env = {"MTDL_EVENT": str(body["event"]), "MTDL_TIME": str(body["time"])}
        env |= {f"MTDL_{name.upper()}": _text(value) for name, value in data.items()}
        env["MTDL_DOWNLOAD_DIR"] = str(self._download_dir)
        # Absolute paths come with the event ("file", "subtitle_file": libraries keep paths relative to themselves);
        # events of older versions only had paths relative to the download folder.
        for field, variable in (("path", "MTDL_FILE"), ("subtitle_path", "MTDL_SUBTITLE_FILE")):
            if data.get(field) and not env.get(variable):
                env[variable] = str(self._download_dir / data[field])
        return env

    def start(self) -> None:
        self._worker = asyncio.create_task(self._work(), name="hooks")

    def _on_event(self, event: Event) -> None:
        if event.name in EVENTS and any(s.executable for s in self.scripts(event.name)):
            self._queue.put_nowait(payload(event))

    async def _work(self) -> None:
        while True:
            body = await self._queue.get()
            try:
                for script in self.scripts(str(body["event"])):
                    if script.executable:
                        await self.run(script, body)
            except Exception:
                log.exception("hooks for %s failed", body["event"])
            finally:
                self._queue.task_done()

    async def run(self, script: Script, body: dict[str, Any]) -> Run:
        started_at = dt.datetime.now(dt.UTC)
        started = time.monotonic()
        tail = _Tail()
        error = None
        try:
            proc = await asyncio.create_subprocess_exec(
                str(script.path),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self._download_dir,
                env=os.environ | self.environment(body),
                start_new_session=True,  # its own process group: the timeout stops children too
            )
        except OSError as exc:  # no shebang, missing interpreter, no permission, no download folder
            return self._done(script, Run(script.event, started_at, 0.0, None, "", f"{type(exc).__name__}: {exc}"))
        self._running = proc
        assert proc.stdin is not None and proc.stdout is not None
        stdin = proc.stdin
        reader = asyncio.create_task(tail.read(proc.stdout))

        async def feed_and_wait() -> None:
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):  # the script does not read stdin
                stdin.write(json.dumps(body, ensure_ascii=False).encode())
                await stdin.drain()
            stdin.close()
            await proc.wait()

        try:
            await asyncio.wait_for(feed_and_wait(), self.timeout)
        except TimeoutError:
            error = f"timeout after {self.timeout:g} s"
            await self._stop(proc)
        finally:
            self._running = None
        try:
            await asyncio.wait_for(asyncio.shield(reader), 2)
        except TimeoutError:
            # A child left running holds the pipe: keep reading, else it blocks once the pipe is full.
            self._draining.add(reader)
            reader.add_done_callback(self._draining.discard)
        exit_code = None if error else proc.returncode
        run = Run(script.event, started_at, round(time.monotonic() - started, 2), exit_code, tail.text(), error)
        return self._done(script, run)

    async def _stop(self, proc: asyncio.subprocess.Process) -> None:
        """SIGTERM to the whole process group, SIGKILL to whatever is left after the grace period: also children that
        ignore SIGTERM or outlive the script."""
        _signal(proc.pid, signal.SIGTERM)
        deadline = time.monotonic() + self._grace
        while time.monotonic() < deadline and _group_alive(proc.pid):
            await asyncio.sleep(0.05)
        _signal(proc.pid, signal.SIGKILL)
        await proc.wait()

    def _done(self, script: Script, run: Run) -> Run:
        self.last[(script.event, script.name)] = run
        ok = run.exit_code == 0
        outcome = "done" if ok else run.error or f"exit code {run.exit_code}"
        level = logging.INFO if ok else logging.WARNING
        log.log(level, "hook %s/%s: %s after %.1f s", script.event, script.name, outcome, run.seconds)
        for line in run.output.splitlines()[-LOG_LINES:]:
            log.log(level, "hook %s: %s", script.name, line)
        return run

    async def drain(self) -> None:
        """Wait until every queued event is through (tests)."""
        await self._queue.join()

    async def close(self) -> None:
        if self._running is not None and self._running.returncode is None:
            _signal(self._running.pid, signal.SIGTERM)
        for task in (self._worker, *self._draining):
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


def _signal(group: int, sig: signal.Signals) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(group, sig)


def _group_alive(group: int) -> bool:
    """Whether a process of the group still exists (a zombie counts until it is reaped)."""
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
