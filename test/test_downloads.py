import asyncio
import contextlib
import dataclasses
import errno
import sqlite3
import stat
import time
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path, PurePosixPath

import httpx
import pytest
from conftest import FULL_EXTRACT, load_films, mvw_results
from fastapi.testclient import TestClient
from test_app import Internet, settings_for

from mtdl_web.app import create_app
from mtdl_web.db import migrate
from mtdl_web.downloads import DownloadError
from mtdl_web.downloads.bandwidth import TokenBucket
from mtdl_web.downloads.manager import DownloadManager, Duplicate, error_code, pick_url
from mtdl_web.downloads.queue import DownloadQueue, film_to_json
from mtdl_web.events import Event, EventBus
from mtdl_web.models import Film
from mtdl_web.naming import Naming, target_path
from mtdl_web.sources.mvw import film_from_mvw
from mtdl_web.state import MIGRATIONS, StateStore

HLS = Path(__file__).parent / "fixtures" / "hls"
SUBTITLE = (Path(__file__).parent / "fixtures" / "subtitles" / "ard-ebutt-mdr.xml").read_bytes()
VIDEO = FULL_EXTRACT.read_bytes()  # real bytes, 165 KB; the manager does not look inside


def mvw(title: str, channel: str | None = None) -> Film:
    (result,) = [r for r in mvw_results() if r["title"] == title and (channel is None or r["channel"] == channel)]
    return film_from_mvw(result)


class MediaServer:
    """CDNs and subtitle servers: everything answers with VIDEO, subtitles with a real EBU-TT file."""

    def __init__(self) -> None:
        self.status: dict[str, int] = {}  # url -> forced status
        self.bodies: dict[str, bytes] = {}  # url -> fixed answer (e.g. a channel logo)
        self.delay = 0.0
        self.requests: list[httpx.Request] = []
        self.active = 0
        self.max_active = 0

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url in self.status:
            return httpx.Response(self.status[url])
        if url in self.bodies:
            return httpx.Response(200, content=self.bodies[url])
        if "subtitle" in url:
            return httpx.Response(200, content=SUBTITLE)
        if url.endswith(".m3u8"):
            return httpx.Response(200, text=(HLS / "orf-chunklist.m3u8").read_text())
        start = int(request.headers.get("range", "bytes=0-").removeprefix("bytes=").split("-")[0] or 0)
        body = VIDEO[start:]
        server = self

        async def stream() -> AsyncIterator[bytes]:
            server.active += 1
            server.max_active = max(server.max_active, server.active)
            try:
                for i in range(0, len(body), 16384):
                    if server.delay:
                        await asyncio.sleep(server.delay)
                    yield body[i : i + 16384]
            finally:
                server.active -= 1

        headers = {"content-length": str(len(body))}
        if start:
            headers["content-range"] = f"bytes {start}-{len(VIDEO) - 1}/{len(VIDEO)}"
        return httpx.Response(206 if start else 200, headers=headers, content=stream())


@dataclasses.dataclass
class Setup:
    manager: DownloadManager
    queue: DownloadQueue
    state: StateStore
    server: MediaServer
    events: list[Event]
    folder: Path


@pytest.fixture
async def setup(tmp_path: Path) -> AsyncIterator[Setup]:
    state = StateStore(tmp_path / "state.sqlite")
    server = MediaServer()
    bus = EventBus()
    events: list[Event] = []
    bus.subscribe(events.append)
    fake_ffmpeg = tmp_path / "ffmpeg"
    fake_ffmpeg.write_text('#!/bin/sh\nfor last; do :; done\nprintf muxed > "$last"\n')
    fake_ffmpeg.chmod(fake_ffmpeg.stat().st_mode | stat.S_IXUSR)
    client = httpx.AsyncClient(transport=httpx.MockTransport(server.handler))
    queue = DownloadQueue(state)
    folder = tmp_path / "downloads"
    manager = DownloadManager(
        queue,
        state.settings,
        client,
        TokenBucket(),
        folder,
        bus,
        ffmpeg=str(fake_ffmpeg),
        retry_delay=0,
        transfer_retry_delay=0,
    )
    task = asyncio.create_task(manager.run())
    yield Setup(manager, queue, state, server, events, folder)
    await manager.close()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await client.aclose()
    state.close()


async def until(predicate: object, seconds: float = 10) -> None:
    deadline = time.monotonic() + seconds
    while not predicate():  # type: ignore[operator]
        assert time.monotonic() < deadline, "timed out"
        await asyncio.sleep(0.02)


def path_for(film: Film) -> PurePosixPath:
    return target_path(film, Naming("episode_number"))


@pytest.mark.anyio
async def test_mp4_with_subtitles(setup: Setup) -> None:
    film = mvw("Das Beste kommt noch! (58)")
    download = await setup.manager.enqueue(film, path_for(film))
    await until(lambda: setup.queue.get(download.id).state == "done")  # type: ignore[union-attr]
    target = setup.folder / "Das Gipfeltreffen/Season 01/Das Gipfeltreffen - S01E58 - Das Beste kommt noch!.mp4"
    assert target.read_bytes() == VIDEO
    srt = target.with_name("Das Gipfeltreffen - S01E58 - Das Beste kommt noch!.de.srt").read_text()
    assert "Der MDR und die Mitteldeutsche\nMedienförderung präsentieren:" in srt
    assert not list(setup.folder.rglob("*.part"))
    assert [e.name for e in setup.events] == ["download.queued", "download.started", "download.completed"]
    assert str(setup.server.requests[0].url) == film.url_hd  # quality "hd" by default
    done = setup.queue.get(download.id)
    assert done is not None and (done.bytes_done, done.attempts) == (len(VIDEO), 1)


@pytest.mark.anyio
async def test_duplicates_across_channels(setup: Setup) -> None:
    ard = mvw("Das ab- und wieder zugesagte Gipfeltreffen", "ARD")
    ndr = mvw("Das ab- und wieder zugesagte Gipfeltreffen", "NDR")  # same broadcast, 120 and 129 s
    first = await setup.manager.enqueue(ard, PurePosixPath("extra 3/a.mp4"))
    with pytest.raises(Duplicate) as caught:
        await setup.manager.enqueue(ndr, PurePosixPath("extra 3/b.mp4"))
    assert caught.value.existing.id == first.id
    forced = await setup.manager.enqueue(ndr, PurePosixPath("extra 3/b.mp4"), force=True)
    assert forced.id != first.id


@pytest.mark.anyio
async def test_temporary_errors_are_retried_then_given_up(setup: Setup) -> None:
    film = mvw("Eulen vor die Säue (57)")
    setup.server.status[film.url_hd or ""] = 503
    download = await setup.manager.enqueue(film, path_for(film))
    await until(lambda: setup.queue.get(download.id).state == "failed", seconds=60)  # type: ignore[union-attr]
    failed = setup.queue.get(download.id)
    assert failed is not None and failed.attempts == 3 and "HTTP 503" in (failed.error or "")
    assert failed.error_code == "http"
    retries = [getattr(e, "will_retry", None) for e in setup.events if e.name == "download.failed"]
    assert retries == [True, True, False]


@pytest.mark.anyio
async def test_forbidden_mentions_the_geo_restriction(setup: Setup) -> None:
    # MediathekViewWeb has no geo field; the film list says this ARD episode is restricted to Germany.
    (listed,) = [f for f in load_films(FULL_EXTRACT) if f.title == "Menschenjagd in gut (39)"]
    assert listed.geo == ("DE",)
    setup.server.status[listed.url_hd or ""] = 403
    download = await setup.manager.enqueue(listed, path_for(listed))
    await until(lambda: setup.queue.get(download.id).state == "failed")  # type: ignore[union-attr]
    failed = setup.queue.get(download.id)
    assert failed is not None and failed.error is not None
    assert failed.error.endswith("(geo restriction: DE)") and failed.attempts == 1
    assert failed.error_code == "geo"


@pytest.mark.anyio
async def test_cancel_removes_partial_files(setup: Setup) -> None:
    setup.server.delay = 0.05
    film = mvw("Gestrickte Wollust (54)")
    download = await setup.manager.enqueue(film, path_for(film))
    await until(lambda: any(setup.folder.rglob("*.part")))
    assert await setup.manager.cancel(download.id)
    cancelled = setup.queue.get(download.id)
    assert cancelled is not None and cancelled.state == "cancelled"
    assert not list(setup.folder.rglob("*.part"))
    assert await setup.manager.cancel(download.id) is False
    assert setup.manager.retry(download.id)
    setup.server.delay = 0
    await until(lambda: setup.queue.get(download.id).state == "done")  # type: ignore[union-attr]


@pytest.mark.anyio
async def test_parallel_limit_follows_the_setting(setup: Setup) -> None:
    setup.state.update_settings({"parallel_downloads": 1})
    setup.server.delay = 0.01
    films = [
        mvw(t)
        for t in ("Schmerz lass nach! (51)", "Berufsunfähig im Bundestag (52)", "Asche macht keine Geräusche (53)")
    ]
    ids = [(await setup.manager.enqueue(f, path_for(f))).id for f in films]
    await until(lambda: all(setup.queue.get(i).state == "done" for i in ids), seconds=30)  # type: ignore[union-attr]
    assert setup.server.max_active == 1


@pytest.mark.anyio
async def test_hls_through_the_manager(setup: Setup) -> None:
    # The orchestration with real playlist data (ORF chunklist); segments and ffmpeg are stand-ins.
    film = dataclasses.replace(
        mvw("Gestrickte Wollust (54)"),
        url_hd="https://apasfiis.sf.apa.at/ipad/x/chunklist_b4165000.m3u8",
        url_subtitle=None,
    )
    download = await setup.manager.enqueue(film, PurePosixPath("orf/zib.mp4"))
    await until(lambda: setup.queue.get(download.id).state == "done")  # type: ignore[union-attr]
    assert (setup.folder / "orf/zib.mp4").read_bytes() == b"muxed"  # written by the stand-in ffmpeg
    assert not (setup.folder / "orf/zib.mp4.parts").exists()
    segment_requests = [r for r in setup.server.requests if str(r.url).endswith(".ts")]
    assert len(segment_requests) == 20


@pytest.mark.anyio
async def test_livestreams_and_missing_urls(setup: Setup) -> None:
    (stream,) = [f for f in load_films(FULL_EXTRACT) if f.title == "3Sat Livestream"]
    with pytest.raises(DownloadError, match="livestream"):
        await setup.manager.enqueue(stream, PurePosixPath("x.mp4"))
    film = mvw("Das Beste kommt noch! (58)")
    assert pick_url(film, "low") == film.url_low
    assert pick_url(dataclasses.replace(film, url_low=None), "low") == film.url
    assert pick_url(dataclasses.replace(film, url_hd=None), "hd") == film.url


@pytest.mark.anyio
async def test_resume_after_a_restart(tmp_path: Path) -> None:
    state = StateStore(tmp_path / "state.sqlite")
    queue = DownloadQueue(state)
    film = mvw("Das Beste kommt noch! (58)")
    path = path_for(film)
    download = queue.add(film, path=str(path), quality="hd", subtitles=False, episode_key="k")
    assert queue.claim_next() is not None  # the process ended while it was running
    part = tmp_path / "dl" / f"{path}.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(VIDEO[:50_000])
    server = MediaServer()
    async with httpx.AsyncClient(transport=httpx.MockTransport(server.handler)) as client:
        manager = DownloadManager(queue, state.settings, client, TokenBucket(), tmp_path / "dl", EventBus())
        task = asyncio.create_task(manager.run())
        await until(lambda: queue.get(download.id).state == "done")  # type: ignore[union-attr]
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert server.requests[0].headers["range"] == "bytes=50000-"
    assert (tmp_path / "dl" / path).read_bytes() == VIDEO
    state.close()


@contextlib.contextmanager
def running_app(
    tmp_path: Path,
    media: MediaServer,
    *,
    servers: dict[str, Callable[[httpx.Request], httpx.Response]] | None = None,
    **overrides: object,
) -> Iterator[TestClient]:
    """The app with the recorded film list and ``media`` as every CDN, after the first import. ``servers``: more
    hosts by name (TMDB, Plex); ``overrides``: settings (library folder, tokens)."""
    internet = Internet()
    extra = servers or {}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host in ("res.mediathekview.de", "liste.mediathekview.de", "mediathekviewweb.de"):
            return internet.handler(request)
        if request.url.host in extra:
            return extra[request.url.host](request)
        return await media.handler(request)

    app = create_app(settings_for(tmp_path, **overrides), transport=httpx.MockTransport(handler))
    with TestClient(app) as client:
        deadline = time.monotonic() + 30
        while client.get("/api/v1/filmlist").json()["films"] != 220:
            assert time.monotonic() < deadline
            time.sleep(0.05)
        yield client


def wait_until_idle(client: TestClient, seconds: float = 30) -> dict[str, int]:
    deadline = time.monotonic() + seconds
    while True:
        counts: dict[str, int] = client.get("/api/v1/downloads", params={"limit": 1}).json()["counts"]
        if not counts.get("queued") and not counts.get("running"):
            return counts
        assert time.monotonic() < deadline, counts
        time.sleep(0.05)


@pytest.fixture
def api(tmp_path: Path) -> Iterator[TestClient]:
    with running_app(tmp_path, MediaServer()) as client:
        yield client


def test_download_through_the_api(api: TestClient) -> None:
    hits = api.get("/api/v1/search", params={"q": "das gipfeltreffen", "limit": 1}).json()["films"]
    response = api.post("/api/v1/downloads", json={"film_id": hits[0]["id"], "naming": {"scheme": "episode_number"}})
    assert response.status_code == 201
    created = response.json()
    assert created["path"] == "Das Gipfeltreffen/Season 01/Das Gipfeltreffen - S01E58 - Das Beste kommt noch!.mp4"
    deadline = time.monotonic() + 10
    while api.get(f"/api/v1/downloads/{created['id']}").json()["state"] != "done":
        assert time.monotonic() < deadline
        time.sleep(0.05)
    again = api.post("/api/v1/downloads", json={"film_id": hits[0]["id"]})
    assert again.status_code == 409
    assert again.json()["detail"] == {"code": "duplicate", "existing_id": created["id"], "state": "done"}
    listing = api.get("/api/v1/downloads").json()
    assert listing["counts"] == {"done": 1} and listing["items"][0]["film"]["title"] == "Das Beste kommt noch! (58)"
    assert api.delete(f"/api/v1/downloads/{created['id']}").status_code == 204


def test_download_a_mediathekviewweb_result(api: TestClient) -> None:
    hit = api.get("/api/v1/search", params={"q": "das gipfeltreffen", "source": "mediathekviewweb", "limit": 3}).json()
    film = {k: v for k, v in hit["films"][2].items() if k != "id"}
    response = api.post("/api/v1/downloads", json={"film": film})
    assert response.status_code == 201, response.text
    assert response.json()["film"]["id"] == hit["films"][2]["id"]  # same id as the film list would give
    bad = api.post("/api/v1/downloads", json={"film_id": "1", "naming": {"scheme": "template", "template": "{genre}"}})
    assert bad.status_code == 404


@pytest.mark.anyio
async def test_gone_is_not_retried(setup: Setup) -> None:
    film = mvw("Eulen vor die Säue (57)")
    setup.server.status[film.url_hd or ""] = 404
    download = await setup.manager.enqueue(film, path_for(film))
    await until(lambda: setup.queue.get(download.id).state == "failed")  # type: ignore[union-attr]
    failed = setup.queue.get(download.id)
    assert failed is not None and (failed.error_code, failed.attempts) == ("gone", 1)


def test_error_codes() -> None:
    film = mvw("Eulen vor die Säue (57)")  # MediathekViewWeb results carry no geo restriction
    assert error_code(PermissionError(errno.EACCES, "Permission denied"), film) == "permission"
    assert error_code(OSError(errno.ENOSPC, "No space left on device"), film) == "disk_full"
    assert error_code(OSError(errno.EROFS, "Read-only file system"), film) == "filesystem"
    assert error_code(DownloadError("timeout", code="network"), film) == "network"
    assert error_code(DownloadError("HTTP 403", status=403), film) == "forbidden"
    assert error_code(DownloadError("HTTP 403", status=403), dataclasses.replace(film, geo=("DE",))) == "geo"
    assert error_code(DownloadError("HTTP 410", status=410), film) == "gone"
    assert error_code(DownloadError("HTTP 500", status=500), film) == "http"
    assert error_code(DownloadError("ffmpeg failed", code="mux"), film) == "mux"
    assert error_code(ValueError("?"), film) == "unknown"


def test_queue_order_can_change(tmp_path: Path) -> None:
    state = StateStore(tmp_path / "state.sqlite")
    queue = DownloadQueue(state)
    titles = ("Eulen vor die Säue (57)", "Gestrickte Wollust (54)", "Das Beste kommt noch! (58)")
    films = [mvw(t) for t in titles]
    ids = [queue.add(f, path=str(path_for(f)), quality="hd", subtitles=False, episode_key=f.title).id for f in films]

    def order() -> list[int]:
        return [d.id for d in queue.entries(("queued",), order="queue")]

    assert order() == ids
    assert queue.move(ids[2], 0)
    assert order() == [ids[2], ids[0], ids[1]]
    assert queue.move(ids[2], 99)  # clamped: the end
    assert order() == ids
    first = queue.claim_next()
    assert first is not None and first.id == ids[0]
    assert not queue.move(ids[0], 1)  # running downloads keep their place
    queue.fail(ids[1], "HTTP 404 for x", code="gone")
    assert queue.get(ids[1]).error_code == "gone"  # type: ignore[union-attr]
    assert queue.retry(ids[1])  # a retried download waits at the end
    assert order() == [ids[2], ids[1]]
    assert queue.get(ids[1]).error_code is None  # type: ignore[union-attr]
    state.close()


def test_migration_keeps_the_queue_order(tmp_path: Path) -> None:
    con = sqlite3.connect(tmp_path / "state.sqlite")
    migrate(con, MIGRATIONS[:3])
    film = mvw("Eulen vor die Säue (57)")
    for _ in range(2):
        con.execute(
            "INSERT INTO download (film_id, film, episode_key, quality, subtitles, path, state, created_at)"
            " VALUES (?, ?, 'key', 'hd', 0, 'a.mp4', 'queued', 0)",
            (film.id, film_to_json(film)),
        )
    con.commit()
    con.close()
    state = StateStore(tmp_path / "state.sqlite")
    queue = DownloadQueue(state)
    assert [(d.id, d.position, d.error_code) for d in queue.entries(order="queue")] == [(1, 1, None), (2, 2, None)]
    added = queue.add(film, path="b.mp4", quality="hd", subtitles=False, episode_key="key")
    assert added.position == 3
    state.close()


def test_reorder_the_queue_through_the_api(tmp_path: Path) -> None:
    media = MediaServer()
    media.delay = 0.2  # each download takes seconds: one runs, the others wait
    with running_app(tmp_path, media) as api:
        assert api.patch("/api/v1/settings", json={"parallel_downloads": 1}).status_code == 200
        hits = api.get("/api/v1/search", params={"q": "das gipfeltreffen", "limit": 3}).json()["films"]
        ids = [api.post("/api/v1/downloads", json={"film_id": h["id"]}).json()["id"] for h in hits]

        def waiting() -> list[int]:
            items = api.get("/api/v1/downloads", params={"state": "queued", "order": "queue"}).json()["items"]
            return [d["id"] for d in items]

        assert waiting() == ids[1:]
        assert api.post(f"/api/v1/downloads/{ids[2]}/move", json={"index": 0}).status_code == 204
        assert waiting() == [ids[2], ids[1]]
        moved = api.post(f"/api/v1/downloads/{ids[0]}/move", json={"index": 1})
        assert (moved.status_code, moved.json()["detail"]["code"]) == (409, "not_queued")
        assert api.post(f"/api/v1/downloads/{ids[1]}/move", json={"index": -1}).status_code == 422
        deadline = time.monotonic() + 30
        while api.get(f"/api/v1/downloads/{ids[2]}").json()["state"] != "running":  # moved ahead, starts second
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert api.get(f"/api/v1/downloads/{ids[0]}").json()["state"] == "done"
        assert waiting() == [ids[1]]
        media.delay = 0
        assert wait_until_idle(api) == {"done": 3}


def test_single_downloads_follow_the_naming_setting(api: TestClient) -> None:
    (hit,) = api.get("/api/v1/search", params={"q": "das gipfeltreffen", "limit": 1}).json()["films"]
    first = api.post("/api/v1/downloads", json={"film_id": hit["id"]}).json()
    assert first["path"] == "Das Gipfeltreffen/Season 01/Das Gipfeltreffen - S01E58 - Das Beste kommt noch!.mp4"
    assert api.patch("/api/v1/settings", json={"naming_scheme": "template"}).status_code == 422  # no template
    unknown = {"naming_scheme": "template", "naming_template": "{genre}/{titel}.{ext}"}
    assert api.patch("/api/v1/settings", json=unknown).status_code == 422
    assert api.patch("/api/v1/settings", json={"naming_scheme": "date"}).status_code == 200
    again = api.post("/api/v1/downloads", json={"film_id": hit["id"], "force": True}).json()
    assert again["path"] == "Das Gipfeltreffen/Das Gipfeltreffen - 2026-08-20 - Das Beste kommt noch! (58).mp4"
    wait_until_idle(api)
