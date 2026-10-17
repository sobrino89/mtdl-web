"""Libraries (libraries.py): the state of 0.1.0.dev19 becomes library "Serien" without anyone's help, libraries
are kept through the API, and a film goes into a movie library under its TMDB name. Real data: the Mediathek entries
and TMDB answers of test_library.py, "Nikita" from the film list of 13.10.2026 (3sat) and TMDB of the same day."""

import datetime as dt
import io
import json
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from conftest import FIXTURES
from fastapi.testclient import TestClient
from test_downloads import MediaServer, running_app, wait_until_idle
from test_library import ANSTALT_DRAFT, TOKEN, FakeTmdb, films, library_app
from test_subscriptions import runtime_of

from mtdl_web.db import migrate
from mtdl_web.downloads.queue import DownloadQueue, film_to_json
from mtdl_web.filmlist.parser import COLUMNS, FilmListReader
from mtdl_web.libraries import DEFAULT_MOVIE_TEMPLATE, Library, LibraryInUse, LibraryStore
from mtdl_web.library import DEFAULT_TEMPLATE, STAGING
from mtdl_web.models import film_id
from mtdl_web.state import MIGRATIONS, StateStore
from mtdl_web.subscriptions import SubscriptionStore

# A real library of 13.10.2026: Plex sees the series folder under the same path, rescans off (Plex watches).
TEMPLATE = "{serie} {tmdb}/{staffelordner}/{serie} - S{staffel}E{folge} - {folgentitel}"


def dev19_database(path: Path, shows: Path) -> None:
    """A state database as 0.1.0.dev19 left it (the shape of a real one on 13.10.2026): two subscriptions into the
    download folder, "Die Anstalt" into the one series library (target and series, no library), downloads into the
    library with absolute paths (done, running, queued, failed) and into the download folder with relative ones."""
    con = sqlite3.connect(path)
    migrate(con, MIGRATIONS[:-1])
    app = {"library_template": TEMPLATE, "plex_library_path": "/data/shows", "plex_url": "http://plex:32400"}
    con.execute("INSERT INTO setting (key, value) VALUES ('app', ?)", (json.dumps(app),))
    naming_downloads = {"scheme": "season_episode", "show": None, "folder": None, "season": None, "template": None}
    namings = [
        ("Das Gipfeltreffen", {**naming_downloads, "target": "downloads", "tmdb_id": None}),
        ("Werwölfe", {**naming_downloads, "target": "downloads", "tmdb_id": None}),
        ("Die Anstalt", {**naming_downloads, "target": "library", "tmdb_id": 73860}),
    ]
    for sid, (name, naming) in enumerate(namings, 1):
        con.execute(
            "INSERT INTO subscription (id, name, query, conditions, naming, state, created_at, updated_at)"
            " VALUES (?, ?, ?, '[]', ?, 'active', 0, 0)",
            (sid, name, name.lower(), json.dumps(naming)),
        )
    anstalt = films("Die Anstalt")
    season = shows / "Die Anstalt {tmdb-73860}" / "Season 13"
    rows = [(f, str(season / f"Die Anstalt - S13E{i:02d}.mp4"), 3, "done") for i, f in enumerate(anstalt[:6], 1)]
    rows += [(anstalt[6], str(season / "Die Anstalt - S13E07.mp4"), 3, "running")]
    rows += [(anstalt[7], str(season / "Die Anstalt - S13E08.mp4"), 3, "queued")]
    rows += [(anstalt[8], str(season / "Die Anstalt - S13E09.mp4"), 3, "failed")]
    gipfel = films("Das Gipfeltreffen")[0]
    rows += [(gipfel, "Das Gipfeltreffen/Season 01/Das Gipfeltreffen - S01E58.mp4", 1, "done")]
    for position, (film, file, sid, state) in enumerate(rows, 1):
        con.execute(
            "INSERT INTO download (film_id, film, subscription_id, episode_key, quality, subtitles, path, state,"
            " created_at, position) VALUES (?, ?, ?, ?, 'hd', 0, ?, ?, 0, ?)",
            (film.id, film_to_json(film), sid, f"key-{position}", file, state, position),
        )
    con.commit()
    con.close()


def test_the_state_of_dev19_becomes_library_serien(tmp_path: Path) -> None:
    shows = tmp_path / "shows"
    dev19_database(tmp_path / "state.sqlite", shows)
    before = {
        r[0]: r[1] for r in sqlite3.connect(tmp_path / "state.sqlite").execute("SELECT id, naming FROM subscription")
    }
    state = StateStore(tmp_path / "state.sqlite")  # the library migration
    libraries = LibraryStore(state)
    adopted = libraries.adopt(shows, state.settings())
    # The settings that applied to the one library are its own now; Plex rescans stay off, as they were.
    assert adopted == Library(1, "Serien", "series", shows, TEMPLATE, False, "/data/shows", False, 1)
    subscriptions = SubscriptionStore(state)
    anstalt = subscriptions.get(3)
    assert anstalt is not None and (anstalt.naming.target, anstalt.naming.tmdb_id, anstalt.naming.library_id) == (
        "library",
        73860,
        1,
    )
    after = {r[0]: r[1] for r in state.db.all("SELECT id, naming FROM subscription")}
    assert after[1] == before[1] and after[2] == before[2]  # into the download folder: untouched
    queue = DownloadQueue(state)
    stored = {d.id: (d.library_id, d.path, d.state) for d in queue.entries(None, limit=100)}
    assert stored[1] == (1, "Die Anstalt {tmdb-73860}/Season 13/Die Anstalt - S13E01.mp4", "done")
    assert stored[8] == (1, "Die Anstalt {tmdb-73860}/Season 13/Die Anstalt - S13E08.mp4", "queued")
    assert stored[9] == (1, "Die Anstalt {tmdb-73860}/Season 13/Die Anstalt - S13E09.mp4", "failed")
    assert stored[10] == (None, "Das Gipfeltreffen/Season 01/Das Gipfeltreffen - S01E58.mp4", "done")

    # Every start adopts again: nothing changes a second time.
    snapshot = state.db.all("SELECT * FROM download ORDER BY id"), state.db.all("SELECT * FROM subscription")
    assert libraries.adopt(shows, state.settings()) == adopted and len(libraries.entries()) == 1
    assert (state.db.all("SELECT * FROM download ORDER BY id"), state.db.all("SELECT * FROM subscription")) == snapshot

    # Deleting waits for the subscription and the unfinished downloads; finished ones keep their absolute path.
    with pytest.raises(LibraryInUse) as caught:
        libraries.delete(1)
    assert (caught.value.subscriptions, caught.value.downloads) == ([3], 3)
    state.db.run("UPDATE subscription SET naming = ? WHERE id = 3", (before[1],))
    state.db.run("UPDATE download SET state = 'cancelled' WHERE state IN ('queued', 'running', 'failed')")
    assert libraries.delete(1) and libraries.entries() == []
    first = queue.get(1)
    assert first is not None and (first.library_id, first.path) == (
        None,
        str(shows / "Die Anstalt {tmdb-73860}" / "Season 13" / "Die Anstalt - S13E01.mp4"),
    )
    assert libraries.adopt(shows, state.settings()) is None and libraries.entries() == []  # deleted stays deleted
    moved = tmp_path / "library" / "shows"  # MTDL_SHOWS_DIR set anew: a library again
    assert libraries.adopt(moved, state.settings()) is not None and [lib.path for lib in libraries.entries()] == [moved]
    state.close()


def test_libraries_through_the_api(tmp_path: Path) -> None:
    hosts: dict[str, Callable[[httpx.Request], httpx.Response]] = {"api.themoviedb.org": FakeTmdb().handler}
    with running_app(tmp_path, MediaServer(), servers=hosts, tmdb_token=TOKEN) as api:
        assert api.get("/api/v1/libraries").json() == []  # no MTDL_SHOWS_DIR: none
        refused = api.post("/api/v1/subscriptions", json=ANSTALT_DRAFT)
        assert refused.json()["detail"]["code"] == "library_unavailable"

        movies = {
            "name": "Filme",
            "kind": "movies",
            "path": str(tmp_path / "movies"),
            "template": DEFAULT_MOVIE_TEMPLATE,
        }
        missing = api.post("/api/v1/libraries", json=movies)
        assert missing.status_code == 422 and missing.json()["detail"]["message"] == "missing (not mounted?)"
        assert not (tmp_path / "movies").exists()  # never made: a library is a mount
        (tmp_path / "movies").mkdir()
        created = api.post("/api/v1/libraries", json={**movies, "path": movies["path"] + "/"})
        assert created.status_code == 201
        film_library = created.json()
        assert (film_library["path"], film_library["problem"], film_library["subscriptions"]) == (
            movies["path"],
            None,
            [],
        )

        def refusal(body: dict[str, Any]) -> tuple[int, str]:
            response = api.post("/api/v1/libraries", json=body)
            return response.status_code, response.json()["detail"]["code"]

        assert refusal({**movies, "name": " filme "}) == (409, "library_name_taken")
        assert refusal({**movies, "name": "Kino", "path": str(tmp_path / "movies" / "Kino")}) == (
            409,
            "library_path_overlap",
        )
        assert refusal({**movies, "name": "Alles", "path": "/"}) == (422, "library_path_invalid")
        assert refusal({**movies, "name": "Config", "path": str(tmp_path / "config")}) == (422, "library_path_invalid")
        assert refusal({**movies, "name": "Kino", "template": "{titel} S{staffel}"}) == (422, "unknown_placeholders")

        example = api.post("/api/v1/libraries/preview", json={"kind": "movies", "template": DEFAULT_MOVIE_TEMPLATE})
        assert example.json() == {"paths": ["Nikita (1990) {tmdb-9322}/Nikita (1990).mp4"]}

        (tmp_path / "shows").mkdir()
        shows = {"name": "Serien", "kind": "series", "path": str(tmp_path / "shows"), "template": DEFAULT_TEMPLATE}
        series_id = api.post("/api/v1/libraries", json=shows).json()["id"]
        sub = api.post("/api/v1/subscriptions", json=ANSTALT_DRAFT).json()  # names no library: the only one
        assert sub["naming"]["library_id"] == series_id
        assert api.get("/api/v1/libraries").json()[1]["subscriptions"] == [sub["id"]]
        wrong_kind = {**ANSTALT_DRAFT, "naming": {**ANSTALT_DRAFT["naming"], "library_id": film_library["id"]}}
        assert api.post("/api/v1/subscriptions", json=wrong_kind).json()["detail"]["code"] == "library_kind"
        (tmp_path / "kids").mkdir()
        api.post("/api/v1/libraries", json={**shows, "name": "Kinderserien", "path": str(tmp_path / "kids")})
        assert api.post("/api/v1/subscriptions", json=ANSTALT_DRAFT).json()["detail"]["code"] == "library_required"

        used = api.put(f"/api/v1/libraries/{series_id}", json={**shows, "kind": "movies", "template": "{titel}"})
        assert used.status_code == 409 and used.json()["detail"]["subscriptions"] == [sub["id"]]
        assert api.delete(f"/api/v1/libraries/{series_id}").json()["detail"]["code"] == "library_in_use"
        renamed = api.put(f"/api/v1/libraries/{series_id}", json={**shows, "name": "TV-Serien"})
        assert renamed.json()["name"] == "TV-Serien" and renamed.json()["path"] == str(tmp_path / "shows")
        assert api.delete(f"/api/v1/libraries/{film_library['id']}").status_code == 204
        assert [lib["name"] for lib in api.get("/api/v1/libraries").json()] == ["TV-Serien", "Kinderserien"]


def nikita(api: TestClient) -> str:
    """Puts the film into the app's film list, as a diff of one entry (the test list has no movies); its id."""
    (film,) = json.loads((FIXTURES / "films-filme-2026-10-13.json").read_text(encoding="utf-8"))["Nikita"]
    when = dt.datetime.fromtimestamp(film["timestamp"], ZoneInfo("Europe/Berlin"))
    hours, rest = divmod(film["duration"], 3600)
    row = [film["channel"], film["topic"], film["title"], when.strftime("%d.%m.%Y"), when.strftime("%H:%M:%S")]
    row += [f"{hours:02d}:{rest // 60:02d}:{rest % 60:02d}", str(film["size"] // 2**20), film.get("description", "")]
    row += [film["url"], film.get("url_website") or "", film.get("url_subtitle") or "", "", film.get("url_low") or ""]
    row += ["", film.get("url_hd") or "", "", str(film["timestamp"]), "", "-".join(film["geo"]), "false"]
    header = ["13.10.2026, 12:00", "13.10.2026, 10:00", "3", "MSearch [Vers.: 3.1.294]", "0" * 32]
    text = f'{{"Filmliste":{json.dumps(header)},"Filmliste":{json.dumps(list(COLUMNS))},"X":{json.dumps(row)}}}'
    runtime_of(api).films.import_diff(FilmListReader(io.StringIO(text)))
    return str(film_id(film["channel"], film["topic"], film["title"], film["url"]))


def test_a_film_goes_into_the_movie_library_under_its_tmdb_name(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []

    def plex(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/library/sections":
            directory = [{"key": "2", "title": "Filme", "type": "movie", "Location": [{"path": "/data/movies"}]}]
            return httpx.Response(200, json={"MediaContainer": {"Directory": directory}})
        return httpx.Response(200)

    media = MediaServer()
    media.delay = 0.05  # slow enough to see the staging folder
    with library_app(tmp_path, media, servers={"plex.local": plex}) as api:
        api.patch("/api/v1/settings", json={"plex_url": "http://plex.local:32400"})
        movies = tmp_path / "movies"
        movies.mkdir()
        library = api.post(
            "/api/v1/libraries",
            json={
                "name": "Filme",
                "kind": "movies",
                "path": str(movies),
                "template": DEFAULT_MOVIE_TEMPLATE,
                "plex_path": "/data/movies",
                "plex_refresh": True,
            },
        ).json()
        found = api.get("/api/v1/metadata/movies", params={"q": "Nikita", "year": 1990}).json()
        assert [(c["tmdb_id"], c["name"], c["year"]) for c in found][:1] == [(9322, "Nikita", 1990)]
        details = api.get("/api/v1/metadata/movies/9322", params={"library_id": library["id"]}).json()
        assert (details["title"], details["year"], details["imdb_id"], details["folders"]) == (
            "Nikita",
            1990,
            "tt0100263",
            [],
        )
        naming = {"target": "library", "library_id": library["id"], "tmdb_id": 9322}
        target = api.post("/api/v1/downloads/target", json={"film_id": nikita(api), "naming": naming}).json()
        file = movies / "Nikita (1990) {tmdb-9322}" / "Nikita (1990).mp4"
        assert target == {
            "library_id": library["id"],
            "path": "Nikita (1990) {tmdb-9322}/Nikita (1990).mp4",
            "file": str(file),
            "exists": False,
            "videos": [],
        }
        series_library = api.get("/api/v1/libraries").json()[0]
        into_series = {"film_id": nikita(api), "naming": {**naming, "library_id": series_library["id"]}}
        assert api.post("/api/v1/downloads", json=into_series).json()["detail"]["code"] == "library_single_download"
        assert (
            api.post("/api/v1/downloads", json={"film_id": nikita(api), "naming": {**naming, "tmdb_id": None}}).json()[
                "detail"
            ]["code"]
            == "movie_missing"
        )

        created = api.post("/api/v1/downloads", json={"film_id": nikita(api), "naming": naming})
        assert created.status_code == 201
        download = created.json()
        assert (download["library_id"], download["path"], download["file"]) == (
            library["id"],
            target["path"],
            str(file),
        )

        def loading() -> None:
            deadline = time.monotonic() + 10
            while not list((movies / STAGING).glob("*/*.part")):  # while it loads: hidden from Plex
                assert time.monotonic() < deadline
                time.sleep(0.01)
            assert not file.parent.exists()  # the movie's folder appears with the finished file

        loading()
        assert api.post(f"/api/v1/downloads/{download['id']}/cancel").json()["state"] == "cancelled"
        assert sorted(p.name for p in movies.iterdir()) == []  # cancelled: no staging, no empty folder
        assert api.post(f"/api/v1/downloads/{download['id']}/retry").is_success
        loading()
        wait_until_idle(api)
        assert file.stat().st_size > 0 and not (movies / STAGING).exists()
        assert api.get(f"/api/v1/downloads/{download['id']}").json()["state"] == "done"
        deadline = time.monotonic() + 5
        while not any(r.url.path.endswith("/refresh") for r in calls):
            assert time.monotonic() < deadline
            time.sleep(0.05)
        refresh = next(r for r in calls if r.url.path.endswith("/refresh"))
        assert refresh.url.path == "/library/sections/2/refresh"
        assert refresh.url.params["path"] == "/data/movies/Nikita (1990) {tmdb-9322}"
        again = api.post("/api/v1/downloads/target", json={"film_id": nikita(api), "naming": naming}).json()
        assert again["exists"] is True
        later = api.get("/api/v1/metadata/movies/9322", params={"library_id": library["id"]}).json()
        assert later["folders"] == ["Nikita (1990) {tmdb-9322}"]


def test_a_movie_folder_of_filebot_takes_the_film(tmp_path: Path) -> None:
    """The movie is there under FileBot's name already: the file joins that folder instead of a second one."""
    hosts: dict[str, Callable[[httpx.Request], httpx.Response]] = {"api.themoviedb.org": FakeTmdb().handler}
    with running_app(tmp_path, MediaServer(), servers=hosts, tmdb_token=TOKEN) as api:
        movies = tmp_path / "movies"
        (movies / "Nikita (1990) [tmdbid-9322]").mkdir(parents=True)
        (movies / "Nikita (1990) [tmdbid-9322]" / "Nikita (1990).mkv").write_bytes(b"")
        (movies / "Nikita (1990) [tmdbid-9322]" / "Nikita (1990).de.srt").write_bytes(b"")
        body = {"name": "Filme", "kind": "movies", "path": str(movies), "template": DEFAULT_MOVIE_TEMPLATE}
        library = api.post("/api/v1/libraries", json=body).json()
        naming = {"target": "library", "library_id": library["id"], "tmdb_id": 9322}
        target = api.post("/api/v1/downloads/target", json={"film_id": nikita(api), "naming": naming}).json()
        assert target["path"] == "Nikita (1990) [tmdbid-9322]/Nikita (1990).mp4"
        assert (target["exists"], target["videos"]) == (False, ["Nikita (1990).mkv"])  # the UI warns, a second copy
