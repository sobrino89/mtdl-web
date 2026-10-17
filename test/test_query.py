import re
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from conftest import FULL_EXTRACT
from fastapi.testclient import TestClient
from test_app import Internet, settings_for
from test_downloads import MediaServer, running_app

from mtdl_web.app import create_app
from mtdl_web.filmlist.parser import open_filmlist
from mtdl_web.filmlist.store import FilmStore, match_expression
from mtdl_web.models import Film
from mtdl_web.query import Query, parse
from mtdl_web.text import index_text, search_text


def test_the_examples_of_the_mediathekviewweb_help() -> None:
    assert parse("!ard !wdr #tatort >80") == Query(
        channels=(("ard",), ("wdr",)), topics=(("tatort",),), min_seconds=4800
    )
    assert parse("!zdfinfo #weltall #universum >30") == Query(
        channels=(("zdfinfo",),), topics=(("weltall",), ("universum",)), min_seconds=1800
    )
    assert parse("!tagesschau24 <10") == Query(channels=(("tagesschau24",),), max_seconds=600)
    assert parse("!ARD !NDR #sturm,der,liebe #rote,rosen") == Query(
        channels=(("ard",), ("ndr",)), topics=(("sturm", "der", "liebe"), ("rote", "rosen"))
    )
    assert parse("+Schokolade *Berlin Nachrichten") == Query(
        words=("nachrichten",), titles=(("schokolade",),), descriptions=(("berlin",),)
    )


def test_what_the_parser_leaves_out() -> None:
    assert parse("Das Gipfeltreffen") == Query(words=("das", "gipfeltreffen"))
    # empty selectors and bounds without a number fall away; ",," stays a word without letters (ignored by the search)
    assert parse("  ! # + * ,, > <abc >1,5 <inf") == Query(words=(",,",), min_seconds=90)
    assert parse(">1e400 <-5").min_seconds is None and parse("<-5").max_seconds is None
    assert parse(">99999999").min_seconds == 100_000 * 60
    assert Query().empty and parse("!!").empty is False and not parse(">0").empty


def _has(text: str, words: tuple[str, ...]) -> bool:
    tokens = re.findall(r"[^\W_]+", index_text(text))
    return all(any(t.startswith(w) for t in tokens) for w in re.findall(r"[^\W_]+", search_text(" ".join(words))))


def _oracle(films: list[Film], text: str) -> set[int]:
    """The syntax in plain Python, to cross-check FTS5 and SQL."""
    q = parse(text)
    fields = (("channel", q.channels), ("topic", q.topics), ("title", q.titles), ("description", q.descriptions))
    hits = set()
    for f in films:
        free = f.title if q.topics else f"{f.topic} {f.title}"
        if q.words and not _has(free, q.words):
            continue
        if not all(any(_has(getattr(f, field), g) for g in groups) for field, groups in fields if groups):
            continue
        if q.min_seconds is not None and (f.duration is None or f.duration < q.min_seconds):
            continue
        if q.max_seconds is not None and (f.duration is None or f.duration > q.max_seconds):
            continue
        hits.add(f.id)
    return hits


@pytest.fixture
def store(tmp_path: Path) -> Iterator[FilmStore]:
    with FilmStore(tmp_path / "films.sqlite", index_description=True) as s, open_filmlist(FULL_EXTRACT) as reader:
        s.import_full(reader)
        yield s


QUERIES = [
    "#das,gipfeltreffen",
    "#gipfeltreffen !mdr",
    "#gipfeltreffen !ard !br",
    "!mdr das beste",
    "#werwölfe +folge,3",
    "#werwolfe +folge !br >20",
    "!3sat <5",
    "!arte.de",
    "!zdf",
    "*sträter",
    "gipfeltreffen >25 <31",
    "#phoenix #37,grad",
    "!nonsense",
]


@pytest.mark.parametrize("text", QUERIES)
def test_local_search_agrees_with_plain_python(store: FilmStore, extract_films: list[Film], text: str) -> None:
    result = store.search(text, limit=1000)
    assert {f.id for f in result.films} == _oracle(extract_films, text), text
    assert result.total == len(result.films)


def test_selectors_on_the_reference_case(store: FilmStore) -> None:
    assert store.search("das gipfeltreffen").total == 35  # unchanged without selectors
    topic = store.search("#das,gipfeltreffen", limit=100).films
    assert len(topic) == 25 and {f.topic for f in topic} == {"Das Gipfeltreffen"}
    numbered = store.search("#das,gipfeltreffen +folge", limit=100).films
    assert numbered == [f for f in topic if _has(f.title, ("folge",))]
    assert {f.channel for f in store.search("!arte", limit=100).films} == {"ARTE.DE", "ARTE.FR"}
    assert {f.channel for f in store.search("!arte.de", limit=100).films} == {"ARTE.DE"}
    assert store.search("!nonsense").total == 0


def test_descriptions_need_their_index(tmp_path: Path) -> None:
    with FilmStore(tmp_path / "films.sqlite", index_description=False) as store, pytest.raises(ValueError):
        store.search("*sträter")


def test_the_api_and_subscriptions_understand_the_syntax(tmp_path: Path, extract_films: list[Film]) -> None:
    text = "!mdr #das,gipfeltreffen >20"
    expected = _oracle(extract_films, text)
    with running_app(tmp_path, MediaServer()) as api:
        found = api.get("/api/v1/search", params={"q": text, "limit": 1000}).json()
        assert {int(f["id"]) for f in found["films"]} == expected and found["total"] == len(expected)
        draft = {"name": "Gipfeltreffen", "query": text, "conditions": []}
        preview = api.post("/api/v1/subscriptions/preview", json=draft).json()
        assert len(preview["items"]) == len(expected)


def test_query_words_are_folded_like_the_index() -> None:
    istanbul = chr(0x130) + "stanbul"  # "İ".lower() is "i" plus a combining dot
    assert match_expression(istanbul) == '{topic title} : ("istanbul"*)'
    assert match_expression("Ko" + chr(0x308) + "ln") == match_expression("Köln") == '{topic title} : ("köln"*)'


def test_subscriptions_check_their_query_before_anything_is_saved(tmp_path: Path) -> None:
    internet = Internet()
    app = create_app(
        settings_for(tmp_path, index_description=False),
        transport=httpx.MockTransport(internet.handler),
        background=False,
    )
    with TestClient(app) as api:
        draft = {"name": "Tatort Münster", "query": "#tatort *münster", "conditions": []}
        preview = api.post("/api/v1/subscriptions/preview", json=draft)
        assert (preview.status_code, preview.json()["detail"]["code"]) == (400, "description_search_unavailable")
        assert api.post("/api/v1/subscriptions", json={**draft, "skip_existing": True}).status_code == 400
        assert api.get("/api/v1/subscriptions").json() == []  # nothing half saved
