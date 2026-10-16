import datetime as dt
import io
import lzma
import zoneinfo
from pathlib import Path

import pytest
from conftest import FULL_EXTRACT, load_films, load_rows, mvw_results

from mtdl_web.filmlist import parser
from mtdl_web.filmlist.parser import URL_RTMP, FilmListError, FilmListReader, open_filmlist
from mtdl_web.models import Film

BERLIN = zoneinfo.ZoneInfo("Europe/Berlin")


def by_title(films: list[Film], title: str) -> Film:
    (film,) = [f for f in films if f.title == title]
    return film


def test_meta() -> None:
    with open_filmlist(FULL_EXTRACT) as reader:
        assert reader.meta.created == dt.datetime(2026, 10, 12, 20, 19, tzinfo=dt.UTC)
        assert reader.meta.list_id == "b1587d20c3b4f75cdbbab50899244d2d"
        assert reader.meta.format_version == "3"


def test_extract_is_read_completely(extract_films: list[Film]) -> None:
    assert len(extract_films) == 220
    assert len({f.id for f in extract_films}) == 220


def test_empty_channel_and_topic_mean_same_as_previous(extract_films: list[Film]) -> None:
    raw = FULL_EXTRACT.read_text(encoding="utf-8")
    assert raw.count('"X":["", ""') > 100  # most entries rely on the rule
    gipfeltreffen = [f for f in extract_films if f.topic == "Das Gipfeltreffen"]
    assert len(gipfeltreffen) == 25
    assert {f.channel for f in gipfeltreffen} == {"ARD", "MDR"}
    werwoelfe = [f for f in extract_films if f.topic == "Werwölfe · Das Spiel von List und Täuschung"]
    assert len(werwoelfe) == 30
    assert {f.channel for f in werwoelfe} == {"BR"}


def test_matches_mediathekviewweb_field_by_field(extract_films: list[Film]) -> None:
    # MVW indexed the same list (12.10.2026 20:19 UTC) and resolves relative urls itself.
    films = {f.url: f for f in extract_films}
    results = mvw_results()
    assert len(results) == 35
    for m in results:
        f = films[m["url_video"]]
        assert (f.channel, f.topic, f.title, f.description) == (m["channel"], m["topic"], m["title"], m["description"])
        assert f.timestamp == m["timestamp"]
        assert f.duration == m["duration"]
        assert f.size == m["size"]
        assert f.url_low == (m["url_video_low"] or None)
        assert f.url_hd == (m["url_video_hd"] or None)
        assert f.url_subtitle == (m["url_subtitle"] or None)
        assert f.url_website == (m["url_website"] or None)


def test_date_and_time_columns_agree_with_timestamp_in_berlin() -> None:
    rows = load_rows(FULL_EXTRACT)
    checked = 0
    for row in rows:
        if row[parser.TIMESTAMP]:
            local = dt.datetime.fromtimestamp(int(row[parser.TIMESTAMP]), BERLIN)
            assert local.strftime("%d.%m.%Y") == row[parser.DATE]
            assert local.strftime("%H:%M:%S").startswith(row[parser.TIME])  # HH:MM:SS, HH:MM or empty
            checked += 1
    assert checked == len(rows) - 3  # two livestreams and one entry without any date


def test_missing_values_become_none(extract_films: list[Film]) -> None:
    assert sum(f.timestamp is None for f in extract_films) == 3
    assert sum(f.duration is None for f in extract_films) == 3
    assert sum(f.size is None for f in extract_films) == 21
    livestream = by_title(extract_films, "3Sat Livestream")
    assert (livestream.topic, livestream.timestamp, livestream.duration) == ("Livestream", None, None)
    assert by_title(extract_films, "Energiewende – Wie kann sie gelingen?").timestamp is None


def test_relative_urls_on_a_broken_main_url(extract_films: list[Film]) -> None:
    # The main url ends in a Java "null"; the low url's offset stops before it.
    film = by_title(extract_films, "Jansons dirigiert Beethoven: Messe in C-Dur, op. 86")
    assert film.url is None
    assert film.url_low == "https://cdn-storage.br.de/b7/2020-05/31/3b63503aa31f11eaa0b0984be10adece_E.mp4"


def test_relative_url_with_short_offset(extract_films: list[Film]) -> None:
    film = by_title(extract_films, "aktueller bericht am Samstag (03.03.2018)")
    assert film.url == "https://srstorage01-a.akamaihd.net/Video/FS/AB/ab_sponsorfrei_2018-03-03_L.mp4"
    assert film.url_low == "https://mediastorage01.sr-online.de/Video/FS/AB/ab_sponsorfrei_2018-03-03_M.mp4"
    assert film.url_hd == "https://srstorage01-a.akamaihd.net/Video/FS/AB/ab_sponsorfrei_2018-03-03_P.mp4"


def test_subtitle_repairs(extract_films: list[Film]) -> None:
    double_scheme = by_title(extract_films, "aktueller bericht am Samstag (03.03.2018)")
    assert double_scheme.url_subtitle == "https://www.ardmediathek.de/subtitle/217579"
    schemeless = by_title(extract_films, "Die Rezeptsucherin in Morbach")
    assert schemeless.url_subtitle == (
        "https://subtitles.swr.de/swr/swr-fernsehen/die-rezeptsucherin/2019/02/21/1097775.xml"
    )
    path_only = by_title(extract_films, "Günter Wallraff undercover im Atommüll-Lager")
    assert path_only.url_subtitle is None
    protocol_relative = by_title(extract_films, "Drakensberge")
    assert protocol_relative.url_subtitle == "https://www.sr-mediathek.de/sr_player/ut.php?file=162122.xml"


def test_website_links_are_http_only() -> None:
    """The interface shows the website as a link, so only http(s) passes. What the real list holds stays (checked on
    all 718,934 values of 12.10.2026): full urls, one bare host, and three tivi.de pages without a scheme."""
    zdf = "https://www.zdf.de/dokumentation/terra-x/index.html"
    assert parser.website_url(f" {zdf} ") == zdf
    assert parser.website_url("https://www.dw.com") == "https://www.dw.com"
    tivi = "www.tivi.de/fernsehen/lassie/start/index.html"
    assert parser.website_url(tivi) == "https://" + tivi
    for bad in ("javascript:alert(1)", "JavaScript://www.zdf.de/%0aalert(1)", "data:text/html,x", "//evil.example/x"):
        assert parser.website_url(bad) is None, bad
    assert parser.website_url('https://www.zdf.de"onmouseover="x') is None
    row = load_rows(FULL_EXTRACT)[0]
    row[parser.WEBSITE] = "javascript:alert(1)"
    assert parser.to_film(row).url_website is None


def test_kika_subtitle_from_rtmp_column(extract_films: list[Film]) -> None:
    rows = [r for r in load_rows(FULL_EXTRACT) if r[URL_RTMP]]
    assert rows
    films = {f.id: f for f in extract_films}
    for row in rows:
        film = films[parser.to_film(row).id]
        assert film.channel == "KiKA"
        assert film.url_subtitle == row[URL_RTMP]


def test_geo(extract_films: list[Film]) -> None:
    geos = {f.geo for f in extract_films}
    assert ("DE", "AT", "CH", "EU") in geos
    assert () in geos


def test_titles_are_kept_verbatim(extract_films: list[Film]) -> None:
    assert any(f.title != f.title.strip() for f in extract_films)
    assert any("\n" in f.title for f in extract_films)


def test_xz_and_compact_form_give_the_same_films(tmp_path: Path, extract_films: list[Film]) -> None:
    compact = FULL_EXTRACT.read_text(encoding="utf-8").replace(',\n"X":', ',"X":').replace(",\n", ",")
    xz = tmp_path / "Filmliste-akt.xz"
    xz.write_bytes(lzma.compress(compact.encode()))
    assert load_films(xz) == extract_films


def test_values_spanning_buffer_boundaries(monkeypatch: pytest.MonkeyPatch, extract_films: list[Film]) -> None:
    monkeypatch.setattr(parser, "_CHUNK", 7)
    assert load_films(FULL_EXTRACT) == extract_films


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[]", "not a JSON object"),
        ('{"X":[]}', "missing film list header"),
        ('{"Filmliste":["a"],"Filmliste":["Sender"]}', "unexpected columns"),
    ],
)
def test_format_errors(text: str, message: str) -> None:
    with pytest.raises(FilmListError, match=message):
        FilmListReader(io.StringIO(text))


def test_truncated_list_is_an_error() -> None:
    raw = FULL_EXTRACT.read_text(encoding="utf-8")
    reader = FilmListReader(io.StringIO(raw[: len(raw) // 2]))
    with pytest.raises(FilmListError):
        list(reader.rows())
