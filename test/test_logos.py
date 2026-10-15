from pathlib import Path

import httpx
import pytest
from test_downloads import MediaServer, running_app

from mtdl_web.logos import SOURCE, LogoStore, logo_file

# A stand-in, not a real logo: the repository carries no broadcaster logos (trademarks), not even in tests.
SVG = (
    b'<?xml version="1.0"?>\n'
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10"/></svg>'
)


def test_channels_of_the_film_list_map_to_the_logo_files() -> None:
    assert logo_file("ZDF-tivi") == "ZDFtivi.svg"
    assert logo_file("ARTE.FR") == logo_file("ARTE.DE") == "arte.svg"
    assert logo_file("Radio Bremen TV") == logo_file("rbtv") == "radio-bremen.svg"
    assert logo_file("3Sat") == "3sat.svg"
    assert logo_file("ORF") is None and logo_file("SRF") is None  # no logo there: the text badge stays


class Github:
    def __init__(self, answer: httpx.Response) -> None:
        self.answer = answer
        self.urls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(str(request.url))
        return self.answer


@pytest.mark.anyio
async def test_fetched_once_then_from_the_cache(tmp_path: Path) -> None:
    github = Github(httpx.Response(200, content=SVG))
    async with httpx.AsyncClient(transport=httpx.MockTransport(github.handler)) as client:
        store = LogoStore(tmp_path / "logos", client)
        assert await store.get("ZDF") == SVG
        assert await store.get("zdf") == SVG
        assert await store.get("ORF") is None
    assert github.urls == [SOURCE + "zdf.svg"]
    assert (tmp_path / "logos" / "zdf.svg").read_bytes() == SVG


@pytest.mark.anyio
@pytest.mark.parametrize("answer", [httpx.Response(404), httpx.Response(200, content=b"<html>rate limited</html>")])
async def test_a_failed_fetch_is_not_repeated_at_once(tmp_path: Path, answer: httpx.Response) -> None:
    github = Github(answer)
    async with httpx.AsyncClient(transport=httpx.MockTransport(github.handler)) as client:
        store = LogoStore(tmp_path / "logos", client)
        assert await store.get("ARD") is None
        assert await store.get("ARD") is None
    assert len(github.urls) == 1
    assert not (tmp_path / "logos" / "ard.svg").exists()


def test_served_only_when_switched_on_and_sandboxed(tmp_path: Path) -> None:
    media = MediaServer()
    media.bodies[SOURCE + "mdr.svg"] = SVG
    with running_app(tmp_path, media) as api:
        assert api.get("/api/v1/logos/MDR").status_code == 404  # off by default
        assert api.patch("/api/v1/settings", json={"channel_logos": True}).status_code == 200
        logo = api.get("/api/v1/logos/MDR")
        assert (logo.status_code, logo.content) == (200, SVG)
        assert logo.headers["content-type"] == "image/svg+xml"
        assert "sandbox" in logo.headers["content-security-policy"]
        assert logo.headers["x-content-type-options"] == "nosniff"
        assert api.get("/api/v1/logos/ORF").json()["detail"]["code"] == "no_logo"
