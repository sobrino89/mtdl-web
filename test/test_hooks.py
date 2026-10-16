import asyncio
import dataclasses
import json
import os
import stat
import time
from pathlib import Path

import pytest
from test_downloads import MediaServer, running_app, wait_until_idle
from test_webhooks import COMPLETED

from mtdl_web.events import EventBus, FilmlistUpdated
from mtdl_web.hooks import OUTPUT_TAIL, HookRunner
from mtdl_web.webhooks import EVENTS, payload


def script(folder: Path, name: str, body: str, *, executable: bool = True) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755 if executable else 0o644)
    return path


@pytest.fixture
def runner(tmp_path: Path) -> HookRunner:
    (tmp_path / "dl").mkdir()
    return HookRunner(tmp_path / "hooks", tmp_path / "dl", EventBus(), timeout=5, grace=0.2)


@pytest.mark.anyio
async def test_a_script_gets_the_event_on_stdin_and_in_the_environment(tmp_path: Path, runner: HookRunner) -> None:
    folder = tmp_path / "hooks" / "download.completed.d"
    script(
        folder,
        "10-record.sh",
        'cat > stdin.json\nprintf "%s\\n" "$MTDL_EVENT" "$MTDL_TITLE" "$MTDL_SIZE" "$MTDL_SUBSCRIPTION_ID"'
        ' "$MTDL_SUBTITLE_PATH" "$MTDL_FILE" "$PWD" > env.txt\necho fertig\n',
    )
    (found,) = runner.scripts("download.completed")
    run = await runner.run(found, payload(COMPLETED))
    assert (run.exit_code, run.output, run.error) == (0, "fertig", None)
    dl = tmp_path / "dl"
    assert json.loads((dl / "stdin.json").read_text()) == payload(COMPLETED)
    assert (dl / "env.txt").read_text().splitlines() == [
        "download.completed",
        "Das Beste kommt noch! (58)",
        "267386880",
        "1",
        "",  # None
        COMPLETED.file,  # absolute, from the event: a library keeps paths relative to itself
        str(dl),
    ]
    assert runner.last[("download.completed", "10-record.sh")] == run
    older = runner.environment(payload(dataclasses.replace(COMPLETED, file="")))  # events before 0.1.0.dev20
    assert older["MTDL_FILE"] == str(dl / COMPLETED.path)


def test_which_files_count(tmp_path: Path, runner: HookRunner) -> None:
    old = os.umask(0)  # UMASK=000 is common on Unraid
    try:
        runner.prepare()
    finally:
        os.umask(old)
    folders = [tmp_path / "hooks", *(tmp_path / "hooks").iterdir()]
    assert sorted(p.name for p in folders[1:]) == [f"{e}.d" for e in sorted(EVENTS)]
    assert {stat.S_IMODE(p.stat().st_mode) for p in folders} == {0o755}  # nobody else may drop a script
    folder = tmp_path / "hooks" / "download.completed.d"
    script(folder, "20-b.py", "")
    script(folder, "10-a.sh", "")
    script(folder, "30-off.sh", "", executable=False)
    script(folder, ".hidden.sh", "")
    script(folder, "40-backup.sh~", "")
    (folder / "50-folder").mkdir()
    listed = [(s.name, s.executable) for s in runner.scripts()]
    assert listed == [("10-a.sh", True), ("20-b.py", True), ("30-off.sh", False)]


@pytest.mark.anyio
async def test_failures_timeouts_and_broken_scripts(tmp_path: Path, runner: HookRunner) -> None:
    folder = tmp_path / "hooks" / "download.failed.d"
    body = payload(COMPLETED)
    failing = script(folder, "fails.sh", 'echo "kein Platz" >&2\nexit 3\n')
    hanging = script(folder, "hangs.sh", "echo start\nsleep 30 &\nsleep 30\n")
    broken = folder / "no-shebang.sh"
    broken.write_text("echo hi\n")
    broken.chmod(0o755)
    scripts = {s.name: s for s in runner.scripts("download.failed")}

    run = await runner.run(scripts[failing.name], body)
    assert (run.exit_code, run.output, run.error) == (3, "kein Platz", None)

    runner.timeout = 0.5
    started = time.monotonic()
    run = await runner.run(scripts[hanging.name], body)
    assert time.monotonic() - started < 4  # stopped with its child (process group), not waiting for "sleep 30"
    assert (run.exit_code, run.output, run.error) == (None, "start", "timeout after 0.5 s")

    run = await runner.run(scripts[broken.name], body)
    assert run.exit_code is None and (run.error or "").startswith("OSError: [Errno 8] Exec format error")


@pytest.mark.anyio
async def test_control_characters_do_not_stop_the_scripts(
    tmp_path: Path, runner: HookRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A NUL in a title (a broken film list) cannot go into the environment: every script still runs, the value on
    one line, and stdin keeps the field as it is."""
    folder = tmp_path / "hooks" / "download.completed.d"
    script(folder, "10-a.sh", 'cat > stdin.json\nprintf "%s" "$MTDL_TITLE" > title.txt\n')
    script(folder, "20-b.sh", "echo b\n")
    body = payload(dataclasses.replace(COMPLETED, title="Titel\x00mit\r\nUmbruch"))
    runs = [await runner.run(found, body) for found in runner.scripts("download.completed")]
    assert [(r.exit_code, r.error) for r in runs] == [(0, None), (0, None)]
    dl = tmp_path / "dl"
    assert (dl / "title.txt").read_text() == "Titel mit Umbruch"
    assert json.loads((dl / "stdin.json").read_text())["data"]["title"] == "Titel\x00mit\r\nUmbruch"

    # Whatever else the system refuses becomes the run's error, not an exception that skips the next scripts.
    monkeypatch.setattr(runner, "environment", lambda body: {"MTDL_X": "a\x00b"})
    run = await runner.run(runner.scripts("download.completed")[0], body)
    assert run.exit_code is None and (run.error or "").startswith("ValueError")


@pytest.mark.anyio
async def test_long_output_keeps_its_end(tmp_path: Path, runner: HookRunner) -> None:
    folder = tmp_path / "hooks" / "download.completed.d"
    script(folder, "chatty.sh", 'i=0\nwhile [ $i -lt 3000 ]; do echo "Zeile $i"; i=$((i+1)); done\n')
    (found,) = runner.scripts()
    run = await runner.run(found, payload(COMPLETED))
    lines = run.output.splitlines()
    assert lines[0] == "…" and lines[1].startswith("Zeile ") and lines[-1] == "Zeile 2999"
    assert len(run.output.encode()) <= OUTPUT_TAIL + 4


@pytest.mark.anyio
async def test_events_run_their_scripts_in_order(tmp_path: Path) -> None:
    (tmp_path / "dl").mkdir()
    bus = EventBus()
    runner = HookRunner(tmp_path / "hooks", tmp_path / "dl", bus, timeout=5)
    log = tmp_path / "dl" / "log.txt"
    for name in ("20-second.sh", "10-first.sh"):
        script(tmp_path / "hooks" / "download.completed.d", name, f'echo "{name} $MTDL_DOWNLOAD_ID" >> log.txt\n')
    script(tmp_path / "hooks" / "filmlist.updated.d", "list.sh", 'echo "list $MTDL_TOTAL" >> log.txt\n')
    runner.start()
    try:
        await bus.publish(COMPLETED)
        await bus.publish(
            FilmlistUpdated(kind="diff", list_id="x", created=COMPLETED.at, added=3, changed=1, removed=0, total=9)
        )
        await runner.drain()
    finally:
        await runner.close()
    assert log.read_text().splitlines() == ["10-first.sh 7", "20-second.sh 7", "list 9"]


def test_scripts_run_after_a_download_and_show_up_in_the_api(tmp_path: Path) -> None:
    hooks = tmp_path / "config" / "hooks" / "download.completed.d"
    script(hooks, "10-size.sh", 'stat -c %s "$MTDL_FILE"\n', executable=False)
    with running_app(tmp_path, MediaServer()) as api:
        listed = api.get("/api/v1/hooks").json()
        assert (listed["directory"], listed["timeout"]) == (str(tmp_path / "config" / "hooks"), 600)
        assert listed["scripts"] == [
            {"event": "download.completed", "name": "10-size.sh", "executable": False, "last_run": None}
        ]
        (hooks / "10-size.sh").chmod(0o755)  # no restart needed
        film = api.get("/api/v1/search", params={"q": "das gipfeltreffen", "limit": 1}).json()["films"][0]
        assert api.post("/api/v1/downloads", json={"film_id": film["id"]}).status_code == 201
        wait_until_idle(api)
        deadline = time.monotonic() + 10
        while not (last := api.get("/api/v1/hooks").json()["scripts"][0]["last_run"]):
            assert time.monotonic() < deadline
            time.sleep(0.05)
        (done,) = api.get("/api/v1/downloads").json()["items"]
        size = (tmp_path / "dl" / done["path"]).stat().st_size
        assert (last["exit_code"], last["error"], last["output"]) == (0, None, str(size))


def _dead(pid: int) -> bool:
    """Gone or a zombie nobody reaped yet (the test container has no init)."""
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(")")[-1].split()[0] == "Z"
    except FileNotFoundError:
        return True


@pytest.mark.anyio
async def test_the_timeout_also_stops_children_that_ignore_sigterm(tmp_path: Path, runner: HookRunner) -> None:
    folder = tmp_path / "hooks" / "download.completed.d"
    script(
        folder, "stubborn.sh", "sh -c 'trap \"\" TERM; echo $$ > child.pid; while :; do sleep 0.1; done' &\nsleep 30\n"
    )
    runner.timeout = 0.5
    (found,) = runner.scripts()
    run = await runner.run(found, payload(COMPLETED))
    assert run.error == "timeout after 0.5 s"
    child = int((tmp_path / "dl" / "child.pid").read_text())
    deadline = time.monotonic() + 3
    while not _dead(child):
        assert time.monotonic() < deadline, "the child survived the timeout"
        await asyncio.sleep(0.05)


@pytest.mark.anyio
async def test_children_that_outlive_their_script_can_keep_writing(tmp_path: Path, runner: HookRunner) -> None:
    folder = tmp_path / "hooks" / "download.completed.d"
    lines = (
        'i=0; while [ $i -lt 4000 ]; do echo "line $i ............................................"; i=$((i+1)); done'
    )
    script(folder, "background.sh", f"(sleep 2.5; {lines}; touch done.flag) &\necho started\n")
    (found,) = runner.scripts()
    run = await runner.run(found, payload(COMPLETED))
    assert (run.exit_code, run.output) == (0, "started")
    flag = tmp_path / "dl" / "done.flag"
    deadline = time.monotonic() + 10
    while not flag.exists():  # 250 KB after the script ended: blocked forever if nobody reads the pipe
        assert time.monotonic() < deadline, "the background child got stuck on a full pipe"
        await asyncio.sleep(0.1)
    await runner.close()


def test_folders_writable_for_others_are_tightened(tmp_path: Path, runner: HookRunner) -> None:
    folder = tmp_path / "hooks" / "download.completed.d"
    folder.mkdir(parents=True)
    for path in (tmp_path / "hooks", folder):
        path.chmod(0o777)  # as dev8 created them with UMASK=000
    runner.prepare()
    assert stat.S_IMODE(folder.stat().st_mode) == stat.S_IMODE((tmp_path / "hooks").stat().st_mode) == 0o755
