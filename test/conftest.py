import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from mtdl_web.filmlist.parser import clean_url, open_filmlist, website_url
from mtdl_web.models import Film, film_id

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


def film_from_mvw(result: dict[str, Any]) -> Film:
    """A film of the recorded MediathekViewWeb answer (the reference case was recorded there). Its values are the
    film list's, relative urls resolved: test_filmlist_parser compares them field by field."""

    def url(key: str) -> str | None:
        return clean_url(result.get(key) or "")

    return Film(
        id=film_id(result["channel"], result["topic"], result["title"], result["url_video"]),
        channel=result["channel"],
        topic=result["topic"],
        title=result["title"],
        description=result.get("description") or "",
        timestamp=result.get("timestamp"),
        duration=result.get("duration") or None,
        size=result.get("size") or None,
        url=url("url_video"),
        url_low=url("url_video_low"),
        url_hd=url("url_video_hd"),
        url_subtitle=url("url_subtitle"),
        url_website=website_url(result.get("url_website") or ""),
        geo=(),
    )


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
