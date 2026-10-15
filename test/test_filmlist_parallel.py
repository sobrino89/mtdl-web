import io
import lzma
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import DIFF_EXTRACT_2019, DIFF_EXTRACT_2133, FULL_EXTRACT, load_films

from mtdl_web.config import Settings, available_cpus
from mtdl_web.filmlist.parallel import ParallelFilmListReader, open_for_import
from mtdl_web.filmlist.parser import FilmListError, FilmListReader
from mtdl_web.filmlist.prepared import Prepared
from mtdl_web.filmlist.store import FilmStore


def sequential(path: Path) -> list[Prepared]:
    with open_for_import(path, workers=1) as reader:
        assert isinstance(reader, FilmListReader)
        return list(reader.prepared())


def parallel(text: str, workers: int = 3, piece_chars: int = 1 << 20) -> list[Prepared]:
    reader = ParallelFilmListReader(io.StringIO(text), workers, piece_chars)
    return list(reader.prepared())


@pytest.mark.parametrize("piece_chars", [1, 700, 20_000, 1 << 20])
def test_same_rows_as_sequential_for_any_piece_size(piece_chars: int) -> None:
    # Pieces of one entry each up to one piece for everything: "same as previous" must cross the boundaries.
    expected = sequential(FULL_EXTRACT)
    assert parallel(FULL_EXTRACT.read_text(encoding="utf-8"), piece_chars=piece_chars) == expected
    assert len(expected) == 220


def test_compact_xz_like_the_server_sends_it(tmp_path: Path) -> None:
    compact = FULL_EXTRACT.read_text(encoding="utf-8").replace(",\n", ",")
    path = tmp_path / "Filmliste-akt.xz"
    path.write_bytes(lzma.compress(compact.encode()))
    with open_for_import(path, workers=4) as reader:
        assert isinstance(reader, ParallelFilmListReader)
        assert reader.meta.list_id == "b1587d20c3b4f75cdbbab50899244d2d"
        assert list(reader.prepared()) == sequential(FULL_EXTRACT)


@pytest.fixture
def stores(tmp_path: Path) -> Iterator[tuple[FilmStore, FilmStore]]:
    with (
        FilmStore(tmp_path / "sequential.sqlite", index_description=True) as one,
        FilmStore(tmp_path / "parallel.sqlite", index_description=True) as other,
    ):
        yield one, other


def test_imports_end_in_the_same_index(stores: tuple[FilmStore, FilmStore]) -> None:
    results = []
    for store, workers in zip(stores, (1, 4), strict=True):
        for path, kind in ((FULL_EXTRACT, "full"), (DIFF_EXTRACT_2019, "diff"), (DIFF_EXTRACT_2133, "diff")):
            with open_for_import(path, workers) as reader:
                r = store.import_full(reader) if kind == "full" else store.import_diff(reader)
            results.append((r.added, r.changed, r.removed, r.total))
    assert results[:3] == results[3:] == [(220, 0, 0, 220), (10, 0, 0, 21), (11, 8, 0, 19)]
    one, other = stores
    films = load_films(FULL_EXTRACT) + load_films(DIFF_EXTRACT_2133)
    assert all(one.get(f.id) == other.get(f.id) for f in films)
    assert one.search("das gipfeltreffen", limit=100) == other.search("das gipfeltreffen", limit=100)


def test_truncated_list_is_an_error() -> None:
    raw = FULL_EXTRACT.read_text(encoding="utf-8")
    with pytest.raises(FilmListError):
        parallel(raw[: len(raw) // 2], piece_chars=5000)


def test_malformed_entry_in_a_worker_is_an_error() -> None:
    raw = FULL_EXTRACT.read_text(encoding="utf-8")
    broken = raw.replace('"X":[', '"X":["only one field"],"X":[', 1)
    with pytest.raises(FilmListError, match="malformed entry"):
        parallel(broken, piece_chars=5000)


def test_empty_list() -> None:
    reader = ParallelFilmListReader(
        io.StringIO(
            '{"Filmliste":["a","12.10.2026, 20:19","3","c","d"],'
            '"Filmliste":["Sender","Thema","Titel","Datum","Zeit","Dauer",'
            '"Größe [MB]","Beschreibung","Url","Website","Url Untertitel",'
            '"Url RTMP","Url Klein","Url RTMP Klein","Url HD","Url RTMP HD",'
            '"DatumL","Url History","Geo","neu"]}'
        ),
        workers=2,
    )
    assert list(reader.prepared()) == []


@pytest.mark.parametrize("text", ["[]", '{"X":[]}', '{"Filmliste":["a"],"Filmliste":["Sender"]}'])
def test_header_errors(text: str) -> None:
    with pytest.raises(FilmListError):
        ParallelFilmListReader(io.StringIO(text), workers=2)


def test_workers_setting() -> None:
    assert Settings.from_env({}).workers == available_cpus() >= 1
    assert Settings.from_env({"MTDL_WORKERS": "3"}).workers == 3
    for bad in ("0", "-2", "zwei", "1.5"):
        with pytest.raises(ValueError, match="MTDL_WORKERS"):
            Settings.from_env({"MTDL_WORKERS": bad})
