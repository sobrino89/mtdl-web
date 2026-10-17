import dataclasses
import datetime as dt
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import FULL_EXTRACT, film_from_mvw, mvw_results

from mtdl_web.api import FilmIn
from mtdl_web.filmlist.parser import open_filmlist
from mtdl_web.filmlist.store import FilmStore
from mtdl_web.models import Film
from mtdl_web.sources.local import LocalSource


@pytest.fixture
def store(tmp_path: Path) -> Iterator[FilmStore]:
    with FilmStore(tmp_path / "films.sqlite", index_description=True) as s, open_filmlist(FULL_EXTRACT) as reader:
        s.import_full(reader)
        yield s


def without_geo(films: list[Film]) -> set[Film]:
    return {dataclasses.replace(f, geo=()) for f in films}


@pytest.mark.anyio
async def test_the_film_list_finds_what_mediathekviewweb_found(store: FilmStore) -> None:
    """The reference query on the local film list gives the 35 films MediathekViewWeb answered on 12.10.2026: same
    ids, same values (its answer has no geo field), newest first."""
    local = await LocalSource(store).search("das gipfeltreffen", limit=50)
    recorded = [film_from_mvw(r) for r in mvw_results()]
    assert local.total == len(recorded) == 35
    assert without_geo(local.films) == without_geo(recorded)
    timestamps = [f.timestamp or 0 for f in local.films]
    assert timestamps == sorted(timestamps, reverse=True)


def test_an_example_film_takes_only_web_links() -> None:
    """Example films of the naming preview come from the request: a website that is not http(s) is dropped."""
    sent = FilmIn(channel="ZDF", topic="t", title="x", url="https://cdn.example/a.mp4", url_website="javascript:x")
    assert sent.to_film().url_website is None


def test_description_search_follows_the_index(store: FilmStore, tmp_path: Path) -> None:
    assert LocalSource(store).description_search
    with FilmStore(tmp_path / "plain.sqlite", index_description=False) as plain:
        assert not LocalSource(plain).description_search


@pytest.mark.anyio
async def test_local_new_since(store: FilmStore) -> None:
    source = LocalSource(store)
    assert await source.new_since(dt.datetime(2026, 10, 12, 20, 18, tzinfo=dt.UTC), limit=5000) != []
    assert await source.new_since(dt.datetime(2026, 10, 12, 20, 19, tzinfo=dt.UTC)) == []
