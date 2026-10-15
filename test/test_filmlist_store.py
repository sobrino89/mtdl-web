import datetime as dt
import io
import json
import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import DIFF_EXTRACT_2019, DIFF_EXTRACT_2133, FULL_EXTRACT, load_films, load_rows, mvw_results

from mtdl_web.filmlist.parser import TITLE, FilmListReader, open_filmlist
from mtdl_web.filmlist.store import _FTS_DDL, FilmStore, ImportRejected, match_expression
from mtdl_web.models import Film
from mtdl_web.text import index_text, search_text

T_2019 = dt.datetime(2026, 10, 12, 20, 19, tzinfo=dt.UTC)
T_2133 = dt.datetime(2026, 10, 12, 21, 33, tzinfo=dt.UTC)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[FilmStore]:
    with FilmStore(tmp_path / "films.sqlite", index_description=True) as s:
        yield s


def import_full(store: FilmStore, path: Path, min_ratio: float = 0.5) -> tuple[int, int, int, int]:
    with open_filmlist(path) as reader:
        r = store.import_full(reader, min_ratio=min_ratio)
    return r.added, r.changed, r.removed, r.total


def import_diff(store: FilmStore, path: Path) -> tuple[int, int, int, int]:
    with open_filmlist(path) as reader:
        r = store.import_diff(reader)
    return r.added, r.changed, r.removed, r.total


def test_full_import_and_reimport(store: FilmStore) -> None:
    assert import_full(store, FULL_EXTRACT) == (220, 0, 0, 220)
    assert store.count() == 220
    assert import_full(store, FULL_EXTRACT) == (0, 0, 0, 220)
    last = store.last_import()
    assert last is not None
    assert (last.kind, last.list_id, last.created) == ("full", "b1587d20c3b4f75cdbbab50899244d2d", T_2019)


def test_films_come_back_unchanged(store: FilmStore) -> None:
    import_full(store, FULL_EXTRACT)
    for film in load_films(FULL_EXTRACT):
        assert store.get(film.id) == film


def test_diffs_add_and_change(store: FilmStore) -> None:
    import_full(store, FULL_EXTRACT)
    # Same creation as the full list: 11 entries identical, 10 missing from the extract.
    assert import_diff(store, DIFF_EXTRACT_2019) == (10, 0, 0, 21)
    # Next list: 8 entries changed (size, ZDF HD variant), 11 new.
    assert import_diff(store, DIFF_EXTRACT_2133) == (11, 8, 0, 19)
    assert store.count() == 241
    film = next(f for f in load_films(DIFF_EXTRACT_2133) if f.title == "L.A. Confidential - Spielfilm, USA 1997")
    stored = store.get(film.id)
    assert stored is not None
    assert stored.url_hd is not None
    assert stored.url_hd.endswith("6660k_p37v17.mp4")


def test_first_seen_survives_changes(store: FilmStore) -> None:
    import_full(store, FULL_EXTRACT)
    import_diff(store, DIFF_EXTRACT_2019)
    import_diff(store, DIFF_EXTRACT_2133)
    known = {f.id for f in load_films(FULL_EXTRACT)} | {f.id for f in load_films(DIFF_EXTRACT_2019)}
    new_in_2133 = {f.id for f in load_films(DIFF_EXTRACT_2133)} - known
    assert len(new_in_2133) == 11
    assert {f.id for f in store.new_since(T_2019)} == new_in_2133
    assert store.new_since(T_2133) == []


def test_diff_replaces_same_identity(store: FilmStore) -> None:
    # A corrected title keeps channel, topic, url and website; the Java client replaces such entries.
    import_full(store, FULL_EXTRACT)
    with open_filmlist(FULL_EXTRACT) as full:
        meta, columns = full.header
    original = next(r for r in load_rows(FULL_EXTRACT) if r[TITLE] == "Eulen vor die Säue (57)")
    corrected = [*original[:TITLE], "Eulen vor die Säue (Folge 57)", *original[TITLE + 1 :]]
    text = f'{{"Filmliste":{json.dumps(meta)},"Filmliste":{json.dumps(columns)},"X":{json.dumps(corrected)}}}'
    reader = FilmListReader(io.StringIO(text))
    result = store.import_diff(reader)
    assert (result.added, result.changed, result.removed) == (1, 0, 1)
    titles = {f.title for f in store.search("eulen säue").films}
    assert titles == {"Eulen vor die Säue (Folge 57)"}


def test_full_import_removes_what_is_gone(store: FilmStore) -> None:
    import_full(store, FULL_EXTRACT)
    # The 2133 extract as a full list: its 8 changed entries stay, the 212 others go.
    assert import_full(store, DIFF_EXTRACT_2133, min_ratio=0) == (11, 8, 212, 19)
    assert store.count() == 19
    assert store.search("").total == 19


def test_shrunken_full_list_is_rejected(store: FilmStore) -> None:
    import_full(store, FULL_EXTRACT)
    with pytest.raises(ImportRejected):
        import_full(store, DIFF_EXTRACT_2019)
    assert store.count() == 220
    last = store.last_import()
    assert last is not None
    assert last.total == 220


def test_search_finds_exactly_the_mediathekviewweb_hits(store: FilmStore) -> None:
    import_full(store, FULL_EXTRACT)
    result = store.search("das gipfeltreffen", limit=100)
    assert result.total == 35
    assert {f.url for f in result.films} == {m["url_video"] for m in mvw_results()}
    timestamps = [f.timestamp or 0 for f in result.films]
    assert timestamps == sorted(timestamps, reverse=True)


def test_search_paging(store: FilmStore) -> None:
    import_full(store, FULL_EXTRACT)
    first = store.search("das gipfeltreffen", limit=20)
    second = store.search("das gipfeltreffen", limit=20, offset=20)
    assert (len(first.films), len(second.films), first.total) == (20, 15, 35)
    assert not {f.id for f in first.films} & {f.id for f in second.films}


def _tokens(text: str, *, indexed: bool = False) -> list[str]:
    return re.findall(r"[^\W_]+", index_text(text) if indexed else search_text(text))


def _oracle(films: list[Film], query: str, include_description: bool) -> set[int]:
    """Plain Python version of the search semantics, to cross-check FTS5."""
    words = _tokens(query)
    hits = set()
    for f in films:
        tokens = _tokens(f.topic + " " + f.title + (" " + f.description if include_description else ""), indexed=True)
        if all(any(t.startswith(w) for t in tokens) for w in words):
            hits.add(f.id)
    return hits


@pytest.mark.parametrize(
    "query", ["das gipfeltreffen", "Sträter", "werwölfe s02", "folge 1", "trump putin", "größten", "heisst"]
)
def test_search_agrees_with_plain_python(store: FilmStore, extract_films: list[Film], query: str) -> None:
    import_full(store, FULL_EXTRACT)
    for include in (False, True):
        result = store.search(query, include_description=include, limit=1000)
        assert {f.id for f in result.films} == _oracle(extract_films, query, include), (query, include)


def test_description_index_can_be_switched_off(tmp_path: Path) -> None:
    path = tmp_path / "films.sqlite"
    with FilmStore(path, index_description=True) as store:
        import_full(store, FULL_EXTRACT)
        with_description = store.search("Sträter", include_description=True).total
        without = store.search("Sträter").total
    assert with_description > without
    with FilmStore(path, index_description=False) as store:
        with pytest.raises(ValueError, match="not indexed"):
            store.search("Sträter", include_description=True)
        assert store.search("Sträter").total == without
        assert store.search("das gipfeltreffen").total == 35
    with FilmStore(path, index_description=True) as store:
        assert store.search("Sträter", include_description=True).total == with_description


def test_wal_is_truncated_after_an_import(tmp_path: Path) -> None:
    path = tmp_path / "films.sqlite"
    with FilmStore(path, index_description=True) as store:
        import_full(store, FULL_EXTRACT)
        wal = path.with_name(path.name + "-wal")
        assert not wal.exists() or wal.stat().st_size == 0


def test_concurrent_queries_from_many_threads(store: FilmStore) -> None:
    # Regression (13.10.2026): sqlite3 shares prepared statements per connection; without the lock in
    # db.Database, "SELECT count(*)" sometimes returned no row while the API polled during an import.
    import concurrent.futures

    import_full(store, FULL_EXTRACT)
    film = load_films(FULL_EXTRACT)[0]

    def hammer(_: int) -> set[object]:
        seen: set[object] = set()
        for _ in range(300):
            seen.add(store.count())
            seen.add(store.get(film.id) == film)
            seen.add(store.search("das gipfeltreffen", limit=5).total)
        return seen

    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        results = set().union(*pool.map(hammer, range(8)))
    assert results == {220, True, 35}


def test_umlauts_are_letters_of_their_own_and_sharp_s_may_be_typed_as_ss_or_s(store: FilmStore) -> None:
    import_full(store, FULL_EXTRACT)  # words of the film list of 12.10.2026
    totals = {q: store.search(q).total for q in ("werwölfe", "WERWÖLFE", "werwö", "werwolfe", "werwoelfe")}
    assert totals == {"werwölfe": 36, "WERWÖLFE": 36, "werwö": 36, "werwolfe": 0, "werwoelfe": 0}
    for spelling in ("größten", "grössten", "grösten"):  # "ß" as "ss" or "s"; the umlaut stays
        assert store.search(spelling).total == 2, spelling  # three times in two films
    assert store.search("grossten").total == store.search("groessten").total == 0
    assert store.search("heisst").total == store.search("heißt").total == 2


def test_sharp_s_finds_only_itself_but_ss_and_s_find_it() -> None:
    con = sqlite3.connect(":memory:")  # the index as the store builds it, with three texts
    con.executescript("CREATE TABLE film (pk INTEGER PRIMARY KEY);" + _FTS_DDL)
    texts = {1: "Mit Maß und Ziel", 2: "Die Masse macht's", 3: "Le Mas des Bories"}
    for pk, text in texts.items():
        con.execute(
            "INSERT INTO film_fts (rowid, topic, title, description) VALUES (?, '', ?, '')", (pk, index_text(text))
        )

    def found(query: str) -> set[int]:
        return {
            pk for (pk,) in con.execute("SELECT rowid FROM film_fts WHERE film_fts MATCH ?", (match_expression(query),))
        }

    assert found("Maß") == {1}  # not "Masse"
    assert found("Mass") == {1, 2}
    assert found("mas") == {1, 2, 3}


def test_an_index_from_before_the_normalization_is_rebuilt(tmp_path: Path) -> None:
    path = tmp_path / "films.sqlite"
    with FilmStore(path, index_description=True) as store:
        import_full(store, FULL_EXTRACT)
    con = sqlite3.connect(path)  # as 0.1.0.dev10 left it: another text form, here simply an empty index
    con.execute("DELETE FROM film_fts")
    con.execute("UPDATE setting SET value = 'german-1' WHERE key = 'fts_text'")
    con.commit()
    con.close()
    with FilmStore(path, index_description=True) as store:
        assert store.search("werwölfe").total == 36
        assert store.search("heisst").total == 2
        assert store.search("das gipfeltreffen").total == 35  # the reference case
