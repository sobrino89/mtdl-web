"""Folders that are not mounted (mounts.py): nothing goes there, and the app says so. Whether a
folder is mounted depends on the machine the tests run on, so the device check is replaced where the outcome
matters; the comparison itself is tested against folders on one device."""

import time
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from test_downloads import MediaServer, running_app, wait_until_idle
from test_library import TOKEN, FakeTmdb, films, only
from test_same_file import Stub
from test_subscriptions import runtime_of

import mtdl_web.downloads.manager as manager_module
from mtdl_web.downloads.manager import DownloadManager
from mtdl_web.downloads.queue import Download
from mtdl_web.libraries import LibraryDraft, LibraryStore
from mtdl_web.library import DEFAULT_TEMPLATE
from mtdl_web.mounts import unmounted
from mtdl_web.runtime import write_test
from mtdl_web.state import StateStore


def test_a_folder_on_the_device_of_the_root_is_not_mounted(tmp_path: Path) -> None:
    assert unmounted(tmp_path / "shows", root=tmp_path)  # missing: its nearest existing parent decides
    (tmp_path / "shows").mkdir()
    assert unmounted(tmp_path / "shows", root=tmp_path)


def test_the_write_test_never_makes_a_library(tmp_path: Path) -> None:
    missing = tmp_path / "shows"
    assert write_test(missing, create=False) == "missing (not mounted?)"
    assert not missing.exists()
    assert write_test(tmp_path / "downloads") is None and (tmp_path / "downloads").is_dir()  # downloads: as before


def test_the_write_test_reports_an_unmounted_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mtdl_web.runtime.unmounted", lambda path: True)
    assert "not mounted" in (write_test(tmp_path, check_mount=True) or "")
    assert write_test(tmp_path, check_mount=False) is None


def download(path: str, library_id: int | None) -> Download:
    film = films("Die Anstalt")[0]
    return Download(
        1, film, None, "", "hd", False, path, "queued", 0, None, None, 0, None, 0, None, None, 1, None, library_id
    )


def test_downloads_go_only_into_known_and_mounted_folders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = StateStore(tmp_path / "state.sqlite")
    libraries = LibraryStore(state)
    shows = libraries.add(LibraryDraft("Serien", "series", tmp_path / "shows", DEFAULT_TEMPLATE)).id
    manager = DownloadManager.__new__(DownloadManager)  # only the folders matter here
    manager._dir, manager._libraries, manager._check_mounts = tmp_path / "dl", libraries, True
    monkeypatch.setattr(manager_module, "unmounted", lambda path: False)
    assert "missing" in (manager._root_problem(download("a/b.mp4", shows)) or "")
    (tmp_path / "shows").mkdir()
    assert manager._root_problem(download("a/b.mp4", shows)) is None
    assert "outside" in (manager._root_problem(download("/data/shows/Die Anstalt/S13E01.mp4", shows)) or "")
    assert "outside" in (manager._root_problem(download("../elsewhere/b.mp4", shows)) or "")
    assert manager._root_problem(download("a.mp4", None)) is None  # the downloads folder may come later
    assert "no longer exists" in (manager._root_problem(download("a.mp4", shows + 1)) or "")
    monkeypatch.setattr(manager_module, "unmounted", lambda path: True)
    assert "not mounted" in (manager._root_problem(download("a/b.mp4", shows)) or "")
    state.close()


def test_a_missing_library_is_reported_and_loads_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Until the library is there its downloads wait (until 0.1.0.dev29 they failed with not_mounted)
    and nothing is written into the container; once it appears they load by themselves."""
    monkeypatch.setattr("mtdl_web.runtime.RECHECK_EVERY", 0.2)
    shows = tmp_path / "shows"  # set, but not mounted (here: not there at all)
    hosts: dict[str, Callable[[httpx.Request], httpx.Response]] = {"api.themoviedb.org": FakeTmdb().handler}
    with running_app(tmp_path, MediaServer(), servers=hosts, shows_dir=shows, tmdb_token=TOKEN) as api:
        assert api.get("/api/v1/libraries").json()[0]["problem"] == "missing (not mounted?)"
        health = api.get("/api/health")  # open without sign-in: no names, paths or error texts
        assert (health.status_code, health.json()["problems"]) == (503, ["library not writable"])
        assert api.get("/api/v1/status").json()["problems"] == ["library Serien not writable: missing (not mounted?)"]
        runtime_of(api).subscriptions._sources = lambda name: Stub(films("Die Anstalt"))
        sub = api.post("/api/v1/subscriptions", json=only("Krankstalt")).json()
        assert api.post(f"/api/v1/subscriptions/{sub['id']}/check").json() == {"queued": 1}
        time.sleep(0.6)  # a few rounds of the folder test
        assert api.get("/api/v1/downloads").json()["counts"] == {"queued": 1}  # waits, does not fail
        assert not shows.exists()  # nothing was written into the container
        shows.mkdir()  # mounted after all
        assert wait_until_idle(api) == {"done": 1}


def test_an_empty_staging_folder_from_before_goes_at_start(tmp_path: Path) -> None:
    staging = tmp_path / "shows" / ".mtdl-incoming"
    staging.mkdir(parents=True)
    (staging / ".plexignore").write_text("*\n")  # as 0.1.0.dev32 left it, without .ignore
    hosts: dict[str, Callable[[httpx.Request], httpx.Response]] = {"api.themoviedb.org": FakeTmdb().handler}
    with running_app(tmp_path, MediaServer(), servers=hosts, shows_dir=tmp_path / "shows", tmdb_token=TOKEN):
        assert not staging.exists() and (tmp_path / "shows").is_dir()
