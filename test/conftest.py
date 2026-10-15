import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from mtdl_web.filmlist.parser import open_filmlist
from mtdl_web.models import Film

FIXTURES = Path(__file__).parent / "fixtures"
FULL_EXTRACT = FIXTURES / "filmliste-auszug-2026-10-12.json"
DIFF_EXTRACT_2019 = FIXTURES / "filmliste-diff-auszug-2026-10-12-2019.json"
DIFF_EXTRACT_2133 = FIXTURES / "filmliste-diff-auszug-2026-10-12-2133.json"
MVW_GIPFELTREFFEN = FIXTURES / "mvw-query-gipfeltreffen-2026-10-12.json"
REAL_FULL_LIST = Path(__file__).parent.parent / "data" / "filmliste" / "Filmliste-akt.xz"


def load_films(path: Path) -> list[Film]:
    with open_filmlist(path) as reader:
        return list(reader.films())


def load_rows(path: Path) -> list[list[str]]:
    with open_filmlist(path) as reader:
        return list(reader.rows())


def mvw_results() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = json.loads(MVW_GIPFELTREFFEN.read_text(encoding="utf-8"))["result"]["results"]
    return results


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def extract_films() -> list[Film]:
    return load_films(FULL_EXTRACT)


@pytest.fixture
def real_full_list() -> Iterator[Path]:
    if not REAL_FULL_LIST.exists():
        pytest.skip(f"real film list missing: {REAL_FULL_LIST}")
    yield REAL_FULL_LIST
