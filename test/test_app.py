import dataclasses
import datetime as dt
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import FIXTURES
from fastapi.testclient import TestClient
from test_filmlist_update import FakeServer
from test_sources import FakeMvw

from mtdl_web import __version__
from mtdl_web.app import create_app
from mtdl_web.config import Settings
from mtdl_web.scheduler import next_run


class Internet:
    """The two servers the app talks to, with recorded real data."""

    def __init__(self) -> None:
        self.filmlist = FakeServer()
        from conftest import DIFF_EXTRACT_2019, FULL_EXTRACT

        self.filmlist.put("Filmliste-akt.xz", FULL_EXTRACT, '"akt"')
        self.filmlist.put("Filmliste-diff.xz", DIFF_EXTRACT_2019, '"diff"')
        self.mvw = FakeMvw()

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "res.mediathekview.de":  # the server directory, real files of 13.10.2026
            name = request.url.path.strip("/").removesuffix(".xml")
            return httpx.Response(200, content=(FIXTURES / f"res-mediathekview-{name}-2026-10-13.xml").read_bytes())
        if request.url.host == "liste.mediathekview.de":
            return self.filmlist.handler(request)
        if request.url.host == "mediathekviewweb.de":
            return self.mvw.handler(request)
        raise AssertionError(f"unexpected request to {request.url}")


def settings_for(tmp_path: Path, **overrides: object) -> Settings:
    """Without sign-in unless a test asks for it (test_auth.py covers access)."""
    base = Settings(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        download_dir=tmp_path / "dl",
        workers=1,
        auth="none",
    )
    return dataclasses.replace(base, **overrides)  # type: ignore[arg-type]


def wait_for(client: TestClient, predicate: Callable[[dict[str, Any]], bool], seconds: float = 30) -> dict[str, Any]:
    deadline = time.monotonic() + seconds
    while True:
        status: dict[str, Any] = client.get("/api/v1/filmlist").json()
        if predicate(status):
            return status
        assert time.monotonic() < deadline, status
        time.sleep(0.05)


@pytest.fixture
def internet() -> Internet:
    return Internet()


@pytest.fixture
def client(tmp_path: Path, internet: Internet) -> Iterator[TestClient]:
    app = create_app(settings_for(tmp_path), transport=httpx.MockTransport(internet.handler))
    with TestClient(app) as c:
        wait_for(c, lambda s: s["films"] == 220 and not s["running"])
        yield c


def test_imports_the_film_list_at_start(client: TestClient) -> None:
    status = client.get("/api/v1/filmlist").json()
    assert status["last_import"]["kind"] == "full"
    assert status["last_import"]["list_id"] == "b1587d20c3b4f75cdbbab50899244d2d"
    assert status["last_error"] is None
    assert status["next_run"] is not None
    assert client.get("/api/health").json() == {"status": "ok", "version": __version__, "problems": []}


def test_search_in_the_film_list(client: TestClient) -> None:
    body = client.get("/api/v1/search", params={"q": "das gipfeltreffen", "limit": 100}).json()
    assert (body["source"], body["total"], len(body["films"])) == ("filmlist", 35, 35)
    newest = body["films"][0]
    assert newest["title"] == "Das Beste kommt noch! (58)"
    assert isinstance(newest["id"], str) and int(newest["id"]) >= 2**53  # beyond JavaScript's safe integers
    assert newest["start"] == "2026-08-20T21:50:00Z"
    assert client.get(f"/api/v1/films/{newest['id']}").json() == newest
    assert client.get("/api/v1/films/123").status_code == 404
    # Not an id, also when Python's int() would take it: other digits, too large for the database, too long.
    for wrong in ("abc", "²", "١٢٣", str(1 << 63), "9" * 20, "9" * 5000):
        assert client.get(f"/api/v1/films/{wrong}").status_code == 404, wrong
        refused = client.post("/api/v1/downloads", json={"film_id": wrong})
        assert (refused.status_code, refused.json()["detail"]["code"]) == (404, "film_not_found"), wrong


def test_search_through_mediathekviewweb(client: TestClient, internet: Internet) -> None:
    body = client.get("/api/v1/search", params={"q": "das gipfeltreffen", "source": "mediathekviewweb"}).json()
    assert (body["source"], body["total"]) == ("mediathekviewweb", 35)
    assert internet.mvw.bodies[0]["queries"][0]["query"] == "das gipfeltreffen"


def test_description_search_follows_the_setting(tmp_path: Path, internet: Internet, client: TestClient) -> None:
    assert client.get("/api/v1/info").json()["description_search"] is True
    with_description = client.get("/api/v1/search", params={"q": "sträter", "description": True}).json()
    assert with_description["total"] > client.get("/api/v1/search", params={"q": "sträter"}).json()["total"]
    off = create_app(
        settings_for(tmp_path / "off", index_description=False),
        transport=httpx.MockTransport(internet.handler),
        background=False,
    )
    with TestClient(off) as c:
        assert c.get("/api/v1/info").json()["description_search"] is False
        response = c.get("/api/v1/search", params={"q": "sträter", "description": True})
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "description_search_unavailable"
        selector = c.get("/api/v1/search", params={"q": "*sträter"})  # the description selector needs it too
        assert (selector.status_code, selector.json()["detail"]["code"]) == (400, "description_search_unavailable")


def test_settings(client: TestClient) -> None:
    settings = client.get("/api/v1/settings").json()
    assert settings["normalize_filenames"] is True and settings["bandwidth_limit"] == 0
    changed = client.patch("/api/v1/settings", json={"bandwidth_limit": 2_500_000, "speed_unit": "MB/s"}).json()
    assert (changed["bandwidth_limit"], changed["speed_unit"]) == (2_500_000, "MB/s")
    assert client.get("/api/v1/settings").json() == changed
    assert client.patch("/api/v1/settings", json={"bandwidth_limit": -1}).status_code == 422
    assert client.patch("/api/v1/settings", json={"no_such_setting": 1}).status_code == 422


def test_update_now(client: TestClient, internet: Internet) -> None:
    before = len(internet.filmlist.requests)
    assert client.post("/api/v1/filmlist/update").status_code == 202
    status = wait_for(client, lambda s: s["last_run"] is not None and not s["running"], seconds=10)
    deadline = time.monotonic() + 10
    while len(internet.filmlist.requests) == before:
        assert time.monotonic() < deadline
        time.sleep(0.05)
    # Diff or full list depends on the time of day (07:00 UTC rule); either way nothing new is imported.
    assert internet.filmlist.requests[-1].url.path in ("/Filmliste-diff.xz", "/Filmliste-akt.xz")
    assert status["last_error"] is None


def test_unwritable_download_folder_degrades_health(tmp_path: Path, internet: Internet) -> None:
    blocked = tmp_path / "dl"
    blocked.write_text("a file where the folder should be")
    app = create_app(
        settings_for(tmp_path, download_dir=blocked), transport=httpx.MockTransport(internet.handler), background=False
    )
    with TestClient(app) as c:
        response = c.get("/api/health")
        assert response.status_code == 503
        assert response.json()["status"] == "degraded"
        assert response.json()["problems"] == ["downloads not writable"]  # no path, no error text
        assert c.post("/api/v1/checks/directories").json()["downloads"] is not None


def test_next_run_is_strictly_later() -> None:
    now = dt.datetime(2026, 10, 13, 10, 17, 30, tzinfo=dt.UTC)
    assert next_run(now, 42) == dt.datetime(2026, 10, 13, 10, 42, tzinfo=dt.UTC)
    assert next_run(now, 17) == dt.datetime(2026, 10, 13, 11, 17, tzinfo=dt.UTC)
    assert next_run(now, 5) == dt.datetime(2026, 10, 13, 11, 5, tzinfo=dt.UTC)


def test_serves_the_ui_beside_the_api(tmp_path: Path, internet: Internet) -> None:
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("<!doctype html><title>mtdl-web</title>")
    (ui / "app.js").write_text("export {};")
    app = create_app(
        settings_for(tmp_path), transport=httpx.MockTransport(internet.handler), background=False, ui_dir=ui
    )
    with TestClient(app) as c:
        page = c.get("/")
        assert page.status_code == 200 and "<title>mtdl-web</title>" in page.text
        # No hashed file names: browsers must revalidate, else an update shows the old UI.
        assert page.headers["cache-control"] == "no-cache"
        assert c.get("/app.js").headers["cache-control"] == "no-cache"
        info = c.get("/api/v1/info").json()
        assert info["version"] == __version__ and info["workers"] == 1
        assert info["download_dir"] == str(tmp_path / "dl")
        assert isinstance(info["uid"], int) and isinstance(info["gid"], int)
        assert c.get("/nothing-here").status_code == 404


def test_system_overview(client: TestClient) -> None:
    system = client.get("/api/v1/system").json()
    assert system["version"] == __version__ and system["films"] == 220
    assert system["film_db_bytes"] > 0 and system["state_db_bytes"] > 0
    assert system["download_free_bytes"] > 0 and system["download_total_bytes"] >= system["download_free_bytes"]
    assert "downloads" in system["writable"] and not any(system["writable"].values())


def test_naming_preview(client: TestClient) -> None:
    gipfel, werwoelfe = "Das Gipfeltreffen", "Werwölfe · Das Spiel von List und Täuschung"
    films = [
        {"channel": "MDR", "topic": gipfel, "title": "Das Beste kommt noch! (58)", "start": "2026-08-20T21:50:00Z"},
        {"channel": "BR", "topic": werwoelfe, "title": "Folge 3: Was für ein Theater (S02/E03)"},
        {"channel": "MDR", "topic": gipfel, "title": f"{gipfel} – Best of 2025", "start": "2025-12-05T11:00:00Z"},
    ]
    body = {"naming": {"scheme": "season_episode"}, "films": films}
    assert client.post("/api/v1/naming/preview", json=body).json()["paths"] == [
        "Das Gipfeltreffen/Season 01/Das Gipfeltreffen - S01E58 - Das Beste kommt noch!.mp4",
        "Werwoelfe - Das Spiel von List und Taeuschung/Season 02/"
        "Werwoelfe - Das Spiel von List und Taeuschung - S02E03 - Folge 3 - Was fuer ein Theater.mp4",
        "Das Gipfeltreffen/Das Gipfeltreffen - 2025-12-05 - Das Gipfeltreffen - Best of 2025.mp4",
    ]
    plain = client.post("/api/v1/naming/preview", json={**body, "normalize": False}).json()["paths"]
    assert plain[1].startswith("Werwölfe · Das Spiel von List und Täuschung/Season 02/")
    template = {"scheme": "template", "template": "{sender}/{jahr}/{titel}.{ext}"}
    assert client.post("/api/v1/naming/preview", json={"naming": template, "films": films[:1]}).json()["paths"] == [
        "MDR/2026/Das Beste kommt noch!.mp4"
    ]
    unknown = {"scheme": "template", "template": "{genre}.{ext}"}
    assert client.post("/api/v1/naming/preview", json={"naming": unknown, "films": films}).status_code == 422


def test_security_headers_and_api_docs_without_cdn(tmp_path: Path, internet: Internet) -> None:
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("<!doctype html><title>mtdl-web</title>")
    app = create_app(
        settings_for(tmp_path), transport=httpx.MockTransport(internet.handler), background=False, ui_dir=ui
    )
    with TestClient(app) as c:
        page = c.get("/")
        csp = page.headers["content-security-policy"]
        for part in (
            "script-src 'self';",
            "frame-ancestors 'none'",
            "media-src 'self' https: http: blob:",  # HLS preview: hls.js plays from a MediaSource
            "connect-src 'self' https: http:",  # and fetches playlist and segments from the CDN
        ):
            assert part in csp, part
        assert page.headers["x-content-type-options"] == "nosniff"
        assert page.headers["referrer-policy"] == "no-referrer"
        api = c.get("/api/v1/info")
        assert api.headers["x-frame-options"] == "DENY" and "content-security-policy" not in api.headers
        docs = c.get("/api/docs")
        assert docs.status_code == 200
        assert "/api-docs/swagger-ui-bundle.js" in docs.text and "/api-docs/swagger-ui.css" in docs.text
        assert "https://" not in docs.text  # no CDN, no external favicon, no online validator
        assert "script-src 'self' 'unsafe-inline'" in docs.headers["content-security-policy"]
        assert c.get("/api/v1/openapi.json").json()["info"]["title"] == "mtdl-web"
