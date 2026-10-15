import dataclasses
import datetime as dt
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import FULL_EXTRACT, MVW_GIPFELTREFFEN, mvw_results

from mtdl_web import USER_AGENT
from mtdl_web.filmlist.parser import open_filmlist
from mtdl_web.filmlist.store import FilmStore
from mtdl_web.http import make_client
from mtdl_web.models import Film
from mtdl_web.sources import Source, SourceError
from mtdl_web.sources.local import LocalSource
from mtdl_web.sources.mvw import MvwSource


class FakeMvw:
    """Answers like api/query, from the recorded response for "das gipfeltreffen" (sorted, paged)."""

    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []
        self.requests: list[httpx.Request] = []
        self.response: httpx.Response | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content)
        self.bodies.append(body)
        if self.response is not None:
            return self.response
        recorded = json.loads(MVW_GIPFELTREFFEN.read_text(encoding="utf-8"))
        results = sorted(recorded["result"]["results"], key=lambda r: r["timestamp"], reverse=True)
        recorded["result"]["results"] = results[body["offset"] : body["offset"] + body["size"]]
        return httpx.Response(200, json=recorded)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[FilmStore]:
    with FilmStore(tmp_path / "films.sqlite", index_description=True) as s, open_filmlist(FULL_EXTRACT) as reader:
        s.import_full(reader)
        yield s


def without_geo(films: list[Film]) -> set[Film]:
    return {dataclasses.replace(f, geo=()) for f in films}


@pytest.mark.anyio
async def test_both_sources_return_the_same_films(store: FilmStore) -> None:
    fake = FakeMvw()
    async with make_client(httpx.MockTransport(fake.handler)) as client:
        sources: list[Source] = [LocalSource(store), MvwSource(client)]
        local, mvw = [await s.search("das gipfeltreffen", limit=50) for s in sources]
    assert local.total == mvw.total == 35
    # Same ids, same values; MVW has no geo field.
    assert without_geo(local.films) == without_geo(mvw.films)
    for result in (local, mvw):  # newest first
        timestamps = [f.timestamp or 0 for f in result.films]
        assert timestamps == sorted(timestamps, reverse=True)
    request = fake.requests[0]
    assert request.headers["content-type"] == "text/plain"
    assert request.headers["user-agent"] == USER_AGENT
    assert fake.bodies[0]["queries"] == [{"fields": ["topic", "title"], "query": "das gipfeltreffen"}]
    assert (fake.bodies[0]["sortBy"], fake.bodies[0]["sortOrder"]) == ("timestamp", "desc")


@pytest.mark.anyio
async def test_description_search(store: FilmStore) -> None:
    fake = FakeMvw()
    async with make_client(httpx.MockTransport(fake.handler)) as client:
        mvw = MvwSource(client)
        await mvw.search("sträter", include_description=True)
    assert fake.bodies[0]["queries"][0]["fields"] == ["topic", "title", "description"]
    assert mvw.description_search
    assert LocalSource(store).description_search


@pytest.mark.anyio
@pytest.mark.parametrize(
    "response",
    [httpx.Response(500), httpx.Response(200, json={"err": ["Invalid query"], "result": None}), httpx.Response(200)],
)
async def test_mvw_errors(response: httpx.Response) -> None:
    fake = FakeMvw()
    fake.response = response
    async with make_client(httpx.MockTransport(fake.handler)) as client:
        with pytest.raises(SourceError):
            await MvwSource(client).search("das gipfeltreffen")


@pytest.mark.anyio
async def test_mvw_new_since_pages_until_older(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MvwSource, "PAGE", 5)
    fake = FakeMvw()
    since = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    async with make_client(httpx.MockTransport(fake.handler)) as client:
        films = await MvwSource(client).new_since(since)
    expected = [r["title"] for r in mvw_results() if r["timestamp"] > since.timestamp()]
    assert sorted(f.title for f in films) == sorted(expected)
    assert len(films) == 8  # episodes 51 to 58
    assert len(fake.requests) == 2  # the second page already reaches back before ``since``
    assert fake.bodies[0]["queries"] == []


@pytest.mark.anyio
async def test_local_new_since(store: FilmStore) -> None:
    source = LocalSource(store)
    assert await source.new_since(dt.datetime(2026, 10, 12, 20, 18, tzinfo=dt.UTC), limit=5000) != []
    assert await source.new_since(dt.datetime(2026, 10, 12, 20, 19, tzinfo=dt.UTC)) == []
