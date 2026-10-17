"""Library target against real data: TMDB answers of 13.10.2026 (trimmed to the fields mtdl-web reads) and the
Mediathek entries of Die Anstalt, Werwölfe and Das Gipfeltreffen from the film list of the same day. The expected
episodes come from joining broadcast day and title with jq on the fixtures, checked by hand, not from the code under
test. File names in the library are the ones FileBot wrote into a real Plex library (read 13.10.2026)."""

import asyncio
import contextlib
import dataclasses
import functools
import json
import time
import zoneinfo
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Any

import httpx
import pytest
from conftest import FIXTURES
from fastapi.testclient import TestClient
from test_downloads import MediaServer, running_app, wait_until_idle
from test_same_file import Stub
from test_subscriptions import runtime_of

from mtdl_web.downloads.manager import DownloadManager
from mtdl_web.http import make_client
from mtdl_web.libraries import LibraryDraft, LibraryStore
from mtdl_web.library import (
    DEFAULT_TEMPLATE,
    STAGING,
    episodes_on_disk,
    id_folders,
    library_path,
    marker,
    match_episode,
    preferred_folder,
)
from mtdl_web.metadata import MetadataCache, MetadataError, Pace, SeriesInfo, Tmdb
from mtdl_web.models import Film
from mtdl_web.state import StateStore

BERLIN = zoneinfo.ZoneInfo("Europe/Berlin")
TOKEN = "eyJ" + "a" * 20 + "." + "b" * 20 + "." + "c" * 20  # the shape of a read access token, not a real one
V3_KEY = "0123456789abcdef0123456789abcdef"
SHOWS = {
    73860: "tmdb-die-anstalt-2026-10-13.json",
    300292: "tmdb-werwoelfe-2026-10-13.json",
    119293: "tmdb-das-gipfeltreffen-2026-10-13.json",
}


class FakeTmdb:
    """api.themoviedb.org as far as mtdl-web uses it, answering with the recorded data."""

    def __init__(self) -> None:
        self.shows = {i: json.loads((FIXTURES / name).read_text(encoding="utf-8")) for i, name in SHOWS.items()}
        self.searches = json.loads((FIXTURES / "tmdb-search-2026-10-13.json").read_text(encoding="utf-8"))
        self.movies = {9322: json.loads((FIXTURES / "tmdb-movie-nikita-2026-10-13.json").read_text(encoding="utf-8"))}
        self.movie_search = json.loads((FIXTURES / "tmdb-search-movie-nikita-2026-10-13.json").read_text("utf-8"))
        self.requests: list[httpx.Request] = []
        self.status: int | None = None  # forced answer
        self.limited = 0  # answer this many requests with 429 first
        self.retry_after: str | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.limited:
            self.limited -= 1
            return httpx.Response(429, headers={"retry-after": self.retry_after} if self.retry_after else {})
        if self.status is not None:
            return httpx.Response(self.status)
        if request.headers.get("authorization") != f"Bearer {TOKEN}" and request.url.params.get("api_key") != V3_KEY:
            return httpx.Response(401, json={"status_code": 7})
        parts = request.url.path.removeprefix("/3/").split("/")
        if parts == ["configuration"]:
            return httpx.Response(200, json={"images": {}})
        if parts == ["search", "tv"]:
            return httpx.Response(200, json=self.searches.get(request.url.params["query"], {"results": []}))
        if parts == ["search", "movie"]:  # recorded for "Nikita", 1990
            found = (request.url.params["query"], request.url.params.get("year")) == ("Nikita", "1990")
            return httpx.Response(200, json=self.movie_search if found else {"results": []})
        if parts[0] == "movie" and int(parts[1]) in self.movies:
            return httpx.Response(200, json=self.movies[int(parts[1])])
        if parts[0] == "tv" and int(parts[1]) in self.shows:
            show = self.shows[int(parts[1])]
            if len(parts) == 2:  # seasons ride along, at most 20 parts; TMDB leaves out what it does not have
                appended = request.url.params.get("append_to_response", "").split(",")
                assert len(appended) <= 20
                seasons = {f"season/{n}": data for n, data in show["seasons"].items() if f"season/{n}" in appended}
                return httpx.Response(200, json={**show["tv"], **seasons})
            if len(parts) == 4 and parts[2] == "season":
                return httpx.Response(200, json=show["seasons"][parts[3]])
        return httpx.Response(404, json={"status_code": 34})


def films(show: str) -> list[Film]:
    data = json.loads((FIXTURES / "films-serien-2026-10-13.json").read_text(encoding="utf-8"))[show]
    return [Film(**{**f, "id": int(f["id"]), "geo": tuple(f["geo"])}) for f in data]


@functools.cache
def series(tmdb_id: int) -> SeriesInfo:
    async def fetch() -> SeriesInfo:
        with TemporaryDirectory() as tmp:
            state = StateStore(Path(tmp) / "state.sqlite")
            try:
                async with make_client(httpx.MockTransport(FakeTmdb().handler)) as client:
                    return await Tmdb(client, lambda: TOKEN, MetadataCache(state.db), rate=1000).series(tmdb_id)
            finally:
                state.close()

    return asyncio.run(fetch())


def matched(show: str, tmdb_id: int) -> dict[str, tuple[tuple[int, int] | None, str]]:
    out = {}
    for film in films(show):
        m = match_episode(film, series(tmdb_id), BERLIN)
        key = f"{film.channel} {film.title}"
        out[key] = ((m.episode.season, m.episode.number) if m.episode else None, m.reason)
    return out


# -- what the title says --------------------------------------------------------------------------------------------


def test_the_marker_at_the_end_of_the_title() -> None:
    assert marker("Folge 3: Was für ein Theater (S02/E03)") == (marker("(S02/E03)")[0], "Folge 3: Was für ein Theater")
    assert marker("Die Prüf-Anstalt - Politsatire (S2026/E01)")[0] == marker("x (S2026/E1)")[0]
    found, rest = marker("Das Beste kommt noch! (58)")
    assert found is not None and found.absolute == 58 and rest == "Das Beste kommt noch!"
    assert marker("Das Gipfeltreffen – Best of 2023 (1/2)")[0] is None  # a part, not an episode
    assert marker("SPEZIAL FOLGE! Werwölfeln (Staffel 7, Folge 15)")[0] == marker("(S7/E15)")[0]


# -- TMDB episodes for Mediathek entries ------------------------------------------------------------------------------


def test_die_anstalt_year_seasons_titles_and_clips() -> None:
    result = matched("Die Anstalt", 73860)
    by_start = {k.split(" ", 1)[1][:30]: v for k, v in result.items()}
    # "(S2024/E06)": the season that started in 2024 is TMDB's 11th; the name confirms it.
    assert by_start["Placebo-Ängste und gefühlte Wa"] == ((11, 6), "number_title")
    assert by_start["SOKO Ampel - Die Politsatire v"] == ((11, 8), "number_title")
    # Titles that do not name the episode: number and day agree.
    assert by_start["Die Anstalt vom 8. April 2025 "] == ((12, 3), "number_date")
    assert by_start["Die Politsatire vom 27. Mai 20"] == ((12, 4), "number_date")
    # Season 13 (2026), the regular episode before the special of the same day and name ("Spezialauftrag (1)").
    assert by_start["Die Prüf-Anstalt - Politsatire"] == ((13, 1), "number_title")
    assert by_start["Die Renten-Anstalt - Politsati"] == ((13, 2), "number_title")
    assert result[
        "ZDF Die Anstalt – Jetzt erst rechts! - Politsatire mit Max Uthoff, Maike Kühl und Claus von Wagner (S2026/E08)"
    ] == ((13, 8), "number_title")
    assert result["3Sat Die Anstalt - Politsatire mit Max Uthoff, Maike Kühl und Claus von Wagner (S2026/E08)"] == (
        (13, 8),
        "number_date",
    )
    episodes = sorted(v[0] for v in result.values() if v[0] is not None)
    assert episodes == [(11, 6), (11, 7), (11, 8)] + [(12, n) for n in range(1, 9)] + [(13, n) for n in range(1, 9)] + [
        (13, 8)
    ]
    # Eight scenes of 2 to 10 minutes (2016 to 2021): no episode, even where they share the day of one.
    assert sum(1 for v in result.values() if v == (None, "no_match")) == 8


def test_werwoelfe_numbers_decide_where_titles_repeat_and_specials_by_name() -> None:
    result = matched("Werwölfe", 300292)
    assert result['BR Folge 1: "Ich bin ein Dorfbewohner" (S01/E01)'] == ((1, 1), "number_title")
    assert result['BR Folge 1: "Ich bin ein Dorfbewohner" (S02/E01)'] == ((2, 1), "number_title")
    assert result["BR Folge 4: All in (S01/E04)"] == ((1, 4), "number_title")
    # ARD was online days to weeks off TMDB's dates: the specials match by name alone.
    assert result["BR Bonus: Lézan und Michael Kessler erkunden das Set"] == ((0, 1), "title")
    assert result["BR Bonus: Valentina erklärt die Spielregeln"] == ((0, 2), "title")
    assert result["BR Creator vs. Cast"] == ((0, 3), "title")
    for unknown in (
        "BR Bonus: Lézan erklärt die Regeln",
        "BR Trailer: Werwölfe · Staffel 1",
        "BR Trailer: Werwölfe · Staffel 2",
    ):
        assert result[unknown] == (None, "no_match")
    numbered = sorted(e for e, reason in result.values() if reason == "number_title" and e is not None)
    assert numbered == [(1, n) for n in range(1, 13)] + [(2, n) for n in range(1, 13)]


def test_das_gipfeltreffen_counts_across_seasons() -> None:
    result = matched("Das Gipfeltreffen", 119293)
    assert result["ARD Menschenjagd in gut (39)"] == ((6, 5), "number_title")
    assert result["MDR Das Beste kommt noch! (58)"] == ((8, 8), "number_title")
    # Specials by their full name ("(1/2)" is a part, no episode marker).
    assert result["MDR Das Gipfeltreffen – Best of 2023 (1/2)"] == ((0, 7), "title_date")
    assert result["MDR Das Gipfeltreffen – Best of 2023 (2/2)"] == ((0, 8), "title_date")
    assert result["MDR Das Gipfeltreffen – Best of 2024 (1/2)"] == ((0, 9), "title")
    assert result["MDR Das Gipfeltreffen – Best of 2024 (2/2)"] == ((0, 10), "title")
    assert result["MDR Das Gipfeltreffen – Best of 2025"] == ((0, 11), "title_date")
    regular = sorted(e for e, reason in result.values() if reason == "number_title" and e is not None)
    assert regular == [(6, n) for n in range(5, 9)] + [(7, n) for n in range(1, 9)] + [(8, n) for n in range(1, 9)]


def test_a_contradiction_and_a_clip_load_nothing() -> None:
    film = next(f for f in films("Werwölfe") if f.title.endswith("(S02/E03)"))
    wrong = dataclasses.replace(film, title="Folge 3: Was für ein Theater (S02/E05)")  # derived: a wrong marker
    assert match_episode(wrong, series(300292), BERLIN).reason == "conflict"
    clip = dataclasses.replace(film, duration=240)  # derived: four minutes of a 25-minute episode
    assert match_episode(clip, series(300292), BERLIN).reason == "too_short"


# -- names and folders ------------------------------------------------------------------------------------------------


def test_the_default_names_are_filebots() -> None:
    werwoelfe = series(300292)
    by_number = {(e.season, e.number): e for e in werwoelfe.episodes}
    film = next(f for f in films("Werwölfe") if f.title.endswith("(S02/E03)"))
    path = library_path(
        film,
        by_number[(2, 3)],
        werwoelfe,
        DEFAULT_TEMPLATE,
        root=Path("/data/shows"),
        existing=None,
        normalize=False,
        timezone=BERLIN,
    )
    assert path == PurePosixPath(
        "/data/shows/Werwölfe – Das Spiel von List und Täuschung {tmdb-300292}/Season 02/"
        "Werwölfe – Das Spiel von List und Täuschung - S02E03 - Was für ein Theater.mp4"
    )
    special = library_path(
        film,
        by_number[(0, 1)],
        werwoelfe,
        DEFAULT_TEMPLATE,
        root=Path("/data/shows"),
        existing=None,
        normalize=False,
        timezone=BERLIN,
    )
    assert special.parent.name == "Specials"
    show = "Werwölfe – Das Spiel von List und Täuschung"
    assert special.name == f"{show} - S00E01 - Bonus - Lézan und Michael Kessler erkunden das Set.mp4"
    plain = library_path(
        film,
        by_number[(2, 3)],
        werwoelfe,
        "{serie} ({serienjahr}) {imdb}/Season {staffel}/{serie} S{staffel}E{folge}",
        root=Path("/x"),
        existing=None,
        normalize=True,
        timezone=BERLIN,
    )
    ascii_show = "Werwoelfe - Das Spiel von List und Taeuschung"
    assert str(plain) == f"/x/{ascii_show} (2025) {{imdb-tt36625953}}/Season 02/{ascii_show} S02E03.mp4"


def test_a_placeholder_name_on_tmdb_gives_way_to_the_mediathek_title() -> None:
    """TMDB lists Das Gipfeltreffen season 9 as "Folge 1" to "Folge 4" (13.10.2026): such a file takes the
    Mediathek's title; season and number stay TMDB's (fine while "S09E01" is in the name)."""
    gipfel = series(119293)
    by_number = {(e.season, e.number): e for e in gipfel.episodes}
    assert [by_number[(9, n)].name for n in range(1, 5)] == ["Folge 1", "Folge 2", "Folge 3", "Folge 4"]
    film = next(f for f in films("Das Gipfeltreffen") if f.title == "Das Beste kommt noch! (58)")

    def name(season: int, number: int) -> str:
        episode = by_number[(season, number)]
        path = library_path(
            film,
            episode,
            gipfel,
            DEFAULT_TEMPLATE,
            root=Path("/data/shows"),
            existing=None,
            normalize=False,
            timezone=BERLIN,
        )
        return path.name

    assert name(9, 1) == "Das Gipfeltreffen - S09E01 - Das Beste kommt noch!.mp4"
    assert name(8, 8) == f"Das Gipfeltreffen - S08E08 - {by_number[(8, 8)].name}.mp4"  # a real name stays


def test_existing_folders_are_found_by_their_id_and_new_episodes_join_them(tmp_path: Path) -> None:
    werwoelfe = series(300292)
    old = tmp_path / "Werwölfe - Das Spiel von List und Täuschung {tvdb-464385}" / "Season 01"
    new = tmp_path / "Werwölfe – Das Spiel von List und Täuschung {tmdb-300292}"
    for folder in (old, new / "Season 02", new / "Specials", tmp_path / "Werwölfe", tmp_path / STAGING):
        folder.mkdir(parents=True)
    (old / "Werwölfe - Das Spiel von List und Täuschung - S01E01 - Ich bin ein Dorfbewohner.mp4").write_bytes(b"")
    (new / "Season 02" / "Werwölfe – Das Spiel von List und Täuschung - S02E03 - Was für ein Theater.mp4").write_bytes(
        b""
    )
    (new / "Specials" / "Werwölfe – Das Spiel von List und Täuschung - S00E01 - Bonus.mp4").write_bytes(b"")
    (new / "Season 02" / "notes S02E04.txt").write_bytes(b"")  # no video
    folders = id_folders(tmp_path, werwoelfe)
    assert [f.name for f in folders] == [old.parent.name, new.name]  # the folder without an ID stays out
    assert episodes_on_disk(folders) == {(1, 1), (2, 3), (0, 1)}
    assert preferred_folder(folders, werwoelfe, DEFAULT_TEMPLATE) == new
    assert preferred_folder(folders, werwoelfe, "{serie} {tvdb}/{staffelordner}/x") == old.parent


def test_the_staging_folder_stays_while_another_download_uses_it(tmp_path: Path) -> None:
    state = StateStore(tmp_path / "state.sqlite")
    libraries = LibraryStore(state)
    libraries.add(LibraryDraft("Serien", "series", tmp_path / "shows", DEFAULT_TEMPLATE))
    staging = tmp_path / "shows" / STAGING
    (staging / "7").mkdir(parents=True)
    (staging / ".plexignore").write_text("*\n")
    (staging / ".ignore").write_text("*\n")
    manager = DownloadManager.__new__(DownloadManager)  # only its libraries matter here
    manager._libraries = libraries
    manager._tidy_staging()
    assert (staging / "7").is_dir() and (staging / ".ignore").exists()  # download 7 still loads
    (staging / "7").rmdir()
    manager._tidy_staging()
    assert not staging.exists()
    state.close()


# -- the TMDB client ---------------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_tmdb_asks_once_then_from_the_cache_and_falls_back_on_failure(tmp_path: Path) -> None:
    server = FakeTmdb()
    state = StateStore(tmp_path / "state.sqlite")
    async with make_client(httpx.MockTransport(server.handler)) as client:
        tmdb = Tmdb(client, lambda: TOKEN, MetadataCache(state.db), rate=1000)
        first = await tmdb.series(73860)
        asked = len(server.requests)
        assert asked == 2  # the series, then its 14 seasons appended to one more request
        assert all(r.headers["user-agent"].startswith("mtdl-web/") for r in server.requests)
        assert all("api_key" not in r.url.params for r in server.requests)
        assert await tmdb.series(73860) == first and len(server.requests) == asked
        assert (first.imdb_id, first.tvdb_id, len(first.episodes)) == ("tt4277128", 279577, 105)
        again = await tmdb.series(73860, max_age=0)  # a refresh asks for the known seasons at once
        assert len(server.requests) == asked + 1 and again.episodes == first.episodes
        server.status = 503
        assert (await tmdb.series(73860, max_age=0)).episodes == first.episodes  # stale beats nothing
        with pytest.raises(MetadataError) as caught:
            await tmdb.series(300292)
        assert caught.value.code == "unavailable"
    state.close()


@pytest.mark.anyio
async def test_tmdb_respects_429_and_keeps_a_v3_key_out_of_errors(tmp_path: Path) -> None:
    server = FakeTmdb()
    state = StateStore(tmp_path / "state.sqlite")
    async with make_client(httpx.MockTransport(server.handler)) as client:
        tmdb = Tmdb(client, lambda: V3_KEY, MetadataCache(state.db), rate=1000, retry_delay=0.01)
        server.limited, server.retry_after = 2, "0"
        found = await tmdb.search("Die Anstalt")
        assert [c.tmdb_id for c in found] == [73860, 65343] and found[0].year == 2014
        assert len(server.requests) == 3 and server.requests[-1].url.params["api_key"] == V3_KEY
        server.limited = 3
        with pytest.raises(MetadataError) as caught:
            await tmdb.search("Die Anstalt")
        assert caught.value.code == "unavailable" and V3_KEY not in str(caught.value)
        bad = Tmdb(client, lambda: "wrong", MetadataCache(state.db), rate=1000)
        with pytest.raises(MetadataError) as caught:
            await bad.test()
        assert caught.value.code == "unauthorized"
        with pytest.raises(MetadataError) as caught:
            await Tmdb(client, lambda: None, MetadataCache(state.db)).test()
        assert caught.value.code == "no_token"
    state.close()


@pytest.mark.anyio
async def test_pace_spaces_requests() -> None:
    pace = Pace(20.0)
    started = time.monotonic()
    for _ in range(4):
        await pace.wait()
    assert time.monotonic() - started >= 0.14  # three gaps of 50 ms


# -- through the app: preview, saving, loading into the library ------------------------------------------------------


ANSTALT_DRAFT: dict[str, Any] = {
    "name": "Die Anstalt",
    "query": "die anstalt",
    "conditions": [{"field": "topic", "operator": "is", "value": "Die Anstalt"}],
    "naming": {"target": "library", "tmdb_id": 73860},
}


def only(title: str) -> dict[str, Any]:
    """The Anstalt subscription narrowed to one episode: one short download per test."""
    rule = {"field": "title", "operator": "contains", "value": title}
    return {**ANSTALT_DRAFT, "conditions": [*ANSTALT_DRAFT["conditions"], rule]}


def library_app(
    tmp_path: Path,
    media: MediaServer | None = None,
    servers: dict[str, Callable[[httpx.Request], httpx.Response]] | None = None,
) -> contextlib.AbstractContextManager[TestClient]:
    hosts = {"api.themoviedb.org": FakeTmdb().handler, **(servers or {})}
    (tmp_path / "shows").mkdir(exist_ok=True)  # the mounted library: mtdl-web never creates it
    return running_app(tmp_path, media or MediaServer(), servers=hosts, shows_dir=tmp_path / "shows", tmdb_token=TOKEN)


def test_settings_show_the_library_but_never_a_token(tmp_path: Path) -> None:
    with library_app(tmp_path) as api:
        assert api.get("/api/v1/info").json()["tmdb"] is True
        (library,) = api.get("/api/v1/libraries").json()  # MTDL_SHOWS_DIR made the first series library
        assert (library["name"], library["kind"], library["path"], library["problem"]) == (
            "Serien",
            "series",
            str(tmp_path / "shows"),
            None,
        )
        state = api.get("/api/v1/integrations").json()
        assert state["tmdb"] == {"configured": True, "source": "env"}
        assert api.put("/api/v1/integrations/secrets", json={"plex_token": "plex-secret"}).json()["plex_token"] == {
            "configured": True,
            "source": "settings",
        }
        for path in ("/api/v1/integrations", "/api/v1/settings", "/api/v1/system"):
            body = api.get(path).text
            assert "plex-secret" not in body and TOKEN not in body
        assert (
            api.put("/api/v1/integrations/secrets", json={"plex_token": None}).json()["plex_token"]["configured"]
            is False
        )
        assert api.post("/api/v1/integrations/tmdb/test").json() == {"ok": True}
        found = api.get("/api/v1/metadata/series", params={"q": "Die Anstalt"}).json()
        assert [s["tmdb_id"] for s in found] == [73860, 65343]
        details = api.get("/api/v1/metadata/series/73860").json()
        assert (details["name"], details["year"], len(details["seasons"]), details["folders"]) == (
            "Die Anstalt",
            2014,
            14,
            [],
        )
        example = api.post("/api/v1/libraries/preview", json={"template": DEFAULT_TEMPLATE}).json()["paths"]
        folder = "Die Anstalt {tmdb-73860}/Season 13"
        assert example[0] == f"{folder}/Die Anstalt - S13E08 - Die Anstalt - Jetzt erst rechts!.mp4"
        assert example[1].endswith("/Specials/Die Anstalt - S00E01 - Spezialauftrag (1) - Die Prüf-Anstalt.mp4")
        assert api.post("/api/v1/libraries/preview", json={"template": "{serie} {quatsch}"}).status_code == 422


def test_preview_save_and_load_into_the_library(tmp_path: Path) -> None:
    media = MediaServer()
    media.delay = 0.05  # slow enough to look at the staging folder while the file loads
    with library_app(tmp_path, media) as api:
        runtime = runtime_of(api)
        runtime.subscriptions._source = lambda: Stub(films("Die Anstalt"))
        shows = tmp_path / "shows"
        season = shows / "Die Anstalt {tmdb-73860}" / "Season 13"
        season.mkdir(parents=True)
        (season / "Die Anstalt - S13E07 - Die AntifAnstalt 100.mkv").write_bytes(b"")  # FileBot was here
        preview = api.post("/api/v1/subscriptions/preview", json=ANSTALT_DRAFT).json()
        counts = preview["counts"]
        assert counts == {"load": 18, "exists": 1, "duplicate": 1, "unmatched": 8}
        loads = [i for i in preview["items"] if i["status"] == "load"]
        newest = max(loads, key=lambda i: i["film"]["start"])
        assert newest["path"] == str(season / "Die Anstalt - S13E08 - Die Anstalt - Jetzt erst rechts!.mp4")
        assert newest["episode"]["name"] == "Die Anstalt - Jetzt erst rechts!" and newest["reason"] == "number_title"
        there = next(i for i in preview["items"] if i["status"] == "exists")
        assert there["in_library"] and there["episode"]["number"] == 7

        assert (
            api.post("/api/v1/subscriptions", json={**ANSTALT_DRAFT, "naming": {"target": "library"}}).json()["detail"][
                "code"
            ]
            == "series_missing"
        )
        newest_only = only("Jetzt erst rechts")
        sub = api.post("/api/v1/subscriptions", json=newest_only).json()
        assert api.post(f"/api/v1/subscriptions/{sub['id']}/check").json() == {"queued": 1}
        deadline = time.monotonic() + 10
        while not list((shows / STAGING).glob("*/*.part")):  # while it loads: hidden from Plex
            assert time.monotonic() < deadline
            time.sleep(0.01)
        for name in (".plexignore", ".ignore"):  # Plex; Jellyfin and Emby
            assert (shows / STAGING / name).read_text().strip().endswith("*")
        wait_until_idle(api)
        target = season / "Die Anstalt - S13E08 - Die Anstalt - Jetzt erst rechts!.mp4"
        assert target.stat().st_size > 0
        assert not (shows / STAGING).exists()  # idle: nothing of mtdl-web in the library but the episode
        assert api.post("/api/v1/subscriptions/preview", json=newest_only).json()["counts"] == {
            "exists": 1,
            "excluded": 27,
        }


def test_plex_reads_the_folder_again_only_when_asked(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []

    def plex(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/library/sections":
            directory = [{"key": "1", "title": "Serien", "type": "show", "Location": [{"path": "/data/shows"}]}]
            return httpx.Response(200, json={"MediaContainer": {"Directory": directory}})
        if request.url.path == "/identity":
            return httpx.Response(200, json={"MediaContainer": {"version": "1.42.2"}})
        return httpx.Response(200)

    with library_app(tmp_path, servers={"plex.local": plex}) as api:
        runtime_of(api).subscriptions._source = lambda: Stub(films("Die Anstalt"))
        assert api.patch("/api/v1/settings", json={"plex_url": "http://plex.local:32400"}).status_code == 200
        (library,) = api.get("/api/v1/libraries").json()
        assert api.put(f"/api/v1/libraries/{library['id']}", json={**library, "plex_path": "/data/shows"}).is_success
        tested = api.post("/api/v1/integrations/plex/test").json()
        assert tested["version"] == "1.42.2" and tested["sections"][0]["paths"] == ["/data/shows"]
        first = api.post("/api/v1/subscriptions", json=only("Krankstalt")).json()
        api.post(f"/api/v1/subscriptions/{first['id']}/check")
        wait_until_idle(api)
        assert not any(r.url.path.endswith("/refresh") for r in calls)  # off by default
        api.put(
            f"/api/v1/libraries/{library['id']}", json={**library, "plex_path": "/data/shows", "plex_refresh": True}
        )
        second = api.post("/api/v1/subscriptions", json=only("Prostitutionsanstalt")).json()
        api.post(f"/api/v1/subscriptions/{second['id']}/check")
        wait_until_idle(api)
        deadline = time.monotonic() + 5
        while not any(r.url.path.endswith("/refresh") for r in calls):
            assert time.monotonic() < deadline
            time.sleep(0.05)
        refresh = next(r for r in calls if r.url.path.endswith("/refresh"))
        assert refresh.url.path == "/library/sections/1/refresh"
        assert refresh.url.params["path"] == "/data/shows/Die Anstalt {tmdb-73860}/Season 13"
        assert refresh.headers["x-plex-product"] == "mtdl-web" and refresh.headers[
            "x-plex-client-identifier"
        ].startswith("mtdl-web-")
        assert "x-plex-token" not in refresh.headers  # none set: Plex in the LAN may allow that


def test_an_episode_tmdb_lists_later_loads_after_the_next_refresh(tmp_path: Path) -> None:
    """A real case (13.10.2026): a new Anstalt episode is in the Mediathek before TMDB lists it. Until then it is
    "unmatched" and loads nothing; the first check after TMDB lists it and the cached answer (6 h) is renewed loads
    it, under TMDB's number and title."""
    server = FakeTmdb()
    season = server.shows[73860]["seasons"]["13"]
    listed = season["episodes"]
    season["episodes"] = [e for e in listed if e["episode_number"] != 8]  # TMDB lists S13E08 two days later
    with library_app(tmp_path, servers={"api.themoviedb.org": server.handler}) as api:
        runtime = runtime_of(api)
        runtime.subscriptions._source = lambda: Stub(films("Die Anstalt"))
        newest = only("Jetzt erst rechts")
        sub = api.post("/api/v1/subscriptions", json=newest).json()
        preview = api.post("/api/v1/subscriptions/preview", json=newest).json()
        (waiting,) = [i for i in preview["items"] if i["status"] != "excluded"]
        assert (waiting["status"], waiting["reason"]) == ("unmatched", "no_match")
        assert api.post(f"/api/v1/subscriptions/{sub['id']}/check").json() == {"queued": 0}

        season["episodes"] = listed
        assert api.post(f"/api/v1/subscriptions/{sub['id']}/check").json() == {"queued": 0}  # the cache is fresh
        runtime.state.db.run("UPDATE metadata_cache SET fetched_at = fetched_at - 6 * 3600")  # six hours on
        assert api.post(f"/api/v1/subscriptions/{sub['id']}/check").json() == {"queued": 1}
        wait_until_idle(api)
        file = tmp_path / "shows" / "Die Anstalt {tmdb-73860}" / "Season 13"
        assert (file / "Die Anstalt - S13E08 - Die Anstalt - Jetzt erst rechts!.mp4").stat().st_size > 0
