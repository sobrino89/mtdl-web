"""Against the real film list in data/filmliste (opt-in: pytest -m full -s). Prints the numbers for the docs.

Worker processes for the parallel import: MTDL_WORKERS, default all available cores.
"""

import hashlib
import sqlite3
import time
from pathlib import Path

import pytest
from conftest import mvw_results
from test_rules import EPISODES, TITLE_NUMBERED, TOPIC_IS, episode

from mtdl_web.config import Settings
from mtdl_web.filmlist.parallel import open_for_import
from mtdl_web.filmlist.store import FilmStore, ImportResult
from mtdl_web.rules.engine import RuleSet

pytestmark = pytest.mark.full

FULL_LIST_ENTRIES = 719_925  # Filmliste-akt of 12.10.2026 20:19 UTC
DIFF_2 = Path(__file__).parent.parent / "data" / "filmliste" / "Filmliste-diff-2.xz"


def import_full(store: FilmStore, path: Path, workers: int) -> ImportResult:
    with open_for_import(path, workers) as reader:
        return store.import_full(reader)


def digest(path: Path) -> str:
    """Everything an import decides, in one value: ids, hashes, first_seen and the full-text index."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    h = hashlib.sha256()
    for row in con.execute("SELECT id, identity, content_hash, first_seen, topic, title FROM film ORDER BY id"):
        h.update(repr(row).encode())
    for (rowid,) in con.execute("SELECT rowid FROM film_fts WHERE film_fts MATCH 'gipfeltreffen OR tatort'"):
        h.update(str(rowid).encode())
    con.close()
    return h.hexdigest()


def check_reference_case(store: FilmStore) -> None:
    hits = store.search("das gipfeltreffen", limit=100)
    assert hits.total == 35
    assert {f.url for f in hits.films} == {m["url_video"] for m in mvw_results()}
    episodes = RuleSet([TOPIC_IS, TITLE_NUMBERED]).matching(hits.films)
    assert sorted(episode(f) for f in episodes) == EPISODES


def test_parallel_import_equals_sequential(tmp_path: Path, real_full_list: Path) -> None:
    workers = Settings.from_env().workers
    paths = {n: tmp_path / f"films-{n}.sqlite" for n in (1, workers)}
    for n, path in paths.items():
        with FilmStore(path, index_description=True) as store:
            result = import_full(store, real_full_list, n)
            assert result.added == result.total == store.count() == FULL_LIST_ENTRIES
            size_mb = store.database_bytes() / 1e6
            print(f"\nworkers={n}: import {result.seconds:.1f} s, database {size_mb:.0f} MB")
            started = time.perf_counter()
            check_reference_case(store)
            print(f"search + rules 'das gipfeltreffen': {(time.perf_counter() - started) * 1000:.0f} ms")
            with_description = store.search("das gipfeltreffen", include_description=True, limit=1)
            print(f"search with description: {with_description.total} hits")
            again = import_full(store, real_full_list, n)
            assert (again.added, again.changed, again.removed) == (0, 0, 0)
            print(f"re-import of the same list: {again.seconds:.1f} s")
            if DIFF_2.exists():
                with open_for_import(DIFF_2, n) as reader:
                    diff = store.import_diff(reader)
                counts = f"+{diff.added} ~{diff.changed} -{diff.removed}"
                print(f"diff 21:33: {diff.total} entries, {counts}, {diff.seconds:.1f} s")
    assert digest(paths[1]) == digest(paths[workers])


def test_without_description_index(tmp_path: Path, real_full_list: Path) -> None:
    with FilmStore(tmp_path / "films.sqlite", index_description=False) as store:
        result = import_full(store, real_full_list, Settings.from_env().workers)
        print(f"\nwithout description: import {result.seconds:.1f} s, database {store.database_bytes() / 1e6:.0f} MB")
        check_reference_case(store)
