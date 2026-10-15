"""Catching downloads again (after the first MCP test): cancel everything or one subscription's
downloads at once, undo by retrying the ids, and load a subscription's cancelled and failed episodes anew."""

import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_downloads import MediaServer, running_app, wait_until_idle
from test_subscriptions import REFERENCE

from mtdl_web.downloads.queue import DownloadQueue
from mtdl_web.state import StateStore


def wait_running(api: TestClient, seconds: float = 10) -> None:
    deadline = time.monotonic() + seconds
    while not api.get("/api/v1/downloads", params={"state": "running"}).json()["items"]:
        assert time.monotonic() < deadline
        time.sleep(0.05)


def test_cancel_all_or_one_subscriptions_and_load_them_again(tmp_path: Path) -> None:
    media = MediaServer()
    media.delay = 0.5  # slow enough that the queue is still there to cancel
    with running_app(tmp_path, media) as api:
        api.patch("/api/v1/settings", json={"parallel_downloads": 1})
        sub = api.post("/api/v1/subscriptions", json={**REFERENCE, "state": "paused"}).json()
        film = api.get("/api/v1/search", params={"q": "!3sat #37,grad", "limit": 1}).json()["films"][0]
        single = api.post("/api/v1/downloads", json={"film_id": film["id"], "quality": "low"}).json()
        assert api.post(f"/api/v1/subscriptions/{sub['id']}/check").json() == {"queued": 20}
        wait_running(api)
        mine = api.get("/api/v1/downloads", params={"subscription": sub["id"], "limit": 100}).json()["items"]
        assert len(mine) == 20 and all(d["subscription_id"] == sub["id"] for d in mine)

        cancelled = api.post("/api/v1/downloads/cancel", params={"subscription": sub["id"]}).json()["cancelled"]
        assert sorted(cancelled) == sorted(d["id"] for d in mine)
        assert api.get(f"/api/v1/downloads/{single['id']}").json()["state"] in ("queued", "running")  # not its own
        everything = api.post("/api/v1/downloads/cancel").json()["cancelled"]
        assert everything == [single["id"]]
        counts = api.get("/api/v1/downloads").json()["counts"]
        assert counts == {"cancelled": 21}
        assert api.post(f"/api/v1/downloads/{cancelled[0]}/retry").json()["state"] == "queued"  # the undo

        media.delay = 0
        again = api.post(f"/api/v1/subscriptions/{sub['id']}/requeue").json()
        assert again == {"forgotten": 19, "queued": 19}  # the retried one waits already
        wait_until_idle(api)
        states = [d["state"] for d in api.get("/api/v1/downloads", params={"subscription": sub["id"]}).json()["items"]]
        assert states == ["done"] * 20
        assert api.post(f"/api/v1/subscriptions/{sub['id']}/requeue").json() == {"forgotten": 0, "queued": 0}
        assert api.get(f"/api/v1/downloads/{single['id']}").json()["state"] == "cancelled"  # single ones stay
        assert api.post("/api/v1/subscriptions/999/requeue").status_code == 404


def test_a_shutdown_with_a_full_queue_starts_nothing_new(tmp_path: Path) -> None:
    """Stopping the container while downloads wait: the running one stays "running" (it resumes after the restart),
    and nothing new starts in the slot it leaves (that one would write into a closed database)."""
    media = MediaServer()
    media.delay = 0.5
    with running_app(tmp_path, media) as api:
        api.patch("/api/v1/settings", json={"parallel_downloads": 1})
        sub = api.post("/api/v1/subscriptions", json={**REFERENCE, "state": "paused"}).json()
        assert api.post(f"/api/v1/subscriptions/{sub['id']}/check").json() == {"queued": 20}
        wait_running(api)
    state = StateStore(tmp_path / "config" / "mtdl-web.sqlite")
    try:
        counts = DownloadQueue(state).counts()
    finally:
        state.close()
    assert counts == {"running": 1, "queued": 19}


def test_downloads_wait_for_a_folder_that_cannot_be_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The UI says downloads wait while the target folder cannot be written; until 0.1.0.dev29
    every one of them failed for good at once. Now they wait (uncounted, with the reason) and go on by themselves once
    the folder works again (Unraid: array stopped, share or mount missing)."""
    monkeypatch.setattr("mtdl_web.runtime.RECHECK_EVERY", 0.2)
    with running_app(tmp_path, MediaServer()) as api:
        folder = tmp_path / "dl"
        shutil.rmtree(folder)
        folder.write_text("")  # a file where the folder was: root ignores permissions, a file stops it
        sub = api.post("/api/v1/subscriptions", json={**REFERENCE, "state": "paused"}).json()
        assert api.post(f"/api/v1/subscriptions/{sub['id']}/check").json() == {"queued": 20}
        deadline = time.monotonic() + 10
        while api.get("/api/health").status_code != 503 or api.get("/api/v1/downloads").json()["counts"].get("running"):
            assert time.monotonic() < deadline
            time.sleep(0.05)
        items = api.get("/api/v1/downloads", params={"subscription": sub["id"], "limit": 100}).json()["items"]
        assert {d["state"] for d in items} == {"queued"}  # waiting, none failed
        tried = [d for d in items if d["error"]]
        assert tried and all(d["error_code"] == "filesystem" and d["attempts"] == 0 for d in tried)
        time.sleep(0.5)  # a few rounds of the folder test: still nothing starts
        assert api.get("/api/v1/downloads").json()["counts"] == {"queued": 20}
        cancelled = api.post(f"/api/v1/downloads/{items[0]['id']}/cancel")  # its folder is gone: nothing to tidy
        assert (cancelled.status_code, cancelled.json()["state"]) == (200, "cancelled")

        folder.unlink()
        folder.mkdir()  # the share is back
        assert wait_until_idle(api) == {"done": 19, "cancelled": 1}
        assert api.get("/api/health").status_code == 200
