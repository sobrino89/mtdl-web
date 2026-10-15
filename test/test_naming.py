import dataclasses
import datetime as dt
import functools
from pathlib import PurePosixPath

import pytest
from conftest import FULL_EXTRACT, load_films, mvw_results

from mtdl_web.models import Film
from mtdl_web.naming import Episode, Naming, clean, episode, target_path, unknown_placeholders
from mtdl_web.sources.mvw import film_from_mvw

# Titles are real (film list of 12.10.2026); docs/filmliste-format.md counts how often each kind of character occurs.


def gipfeltreffen(title: str) -> Film:
    (result,) = [r for r in mvw_results() if r["title"] == title]
    return film_from_mvw(result)


@functools.cache
def extract() -> list[Film]:
    return load_films(FULL_EXTRACT)


def werwoelfe(title: str) -> Film:
    (film,) = [f for f in extract() if f.title == title and f.channel == "BR"]
    return film


def test_episode_markers() -> None:
    assert episode("Das Beste kommt noch! (58)") == Episode(None, 58, "Das Beste kommt noch!")
    assert episode("Folge 3: Was für ein Theater (S02/E03)") == Episode(2, 3, "Folge 3: Was für ein Theater")
    assert episode("Gipfeltreffen in Alaska - was heißt das für die Ukraine? (S2025/E44)").season == 2025
    assert episode("Das Gipfeltreffen – Best of 2023 (1/2)") == Episode(
        None, None, "Das Gipfeltreffen – Best of 2023 (1/2)"
    )


def test_episode_number_scheme_reference_case() -> None:
    film = gipfeltreffen("Das Beste kommt noch! (58)")
    path = target_path(film, Naming("episode_number"))
    assert path == PurePosixPath("Das Gipfeltreffen/Season 01/Das Gipfeltreffen - S01E58 - Das Beste kommt noch!.mp4")


def test_season_episode_scheme_with_umlauts() -> None:
    film = werwoelfe("Folge 3: Was für ein Theater (S02/E03)")
    show = "Werwoelfe - Das Spiel von List und Taeuschung"
    assert target_path(film, Naming("season_episode")) == PurePosixPath(
        show, "Season 02", f"{show} - S02E03 - Folge 3 - Was fuer ein Theater.mp4"
    )
    plain = target_path(film, Naming("season_episode"), normalize=False)
    assert plain.name == "Werwölfe · Das Spiel von List und Täuschung - S02E03 - Folge 3 - Was für ein Theater.mp4"


def test_date_scheme_and_the_bonus_episode() -> None:
    # The episode from the report: "é" and ":" ended up in the file name (MediathekView, 13.10.2026).
    film = werwoelfe("Bonus: Lézan erklärt die Regeln")  # 23.10.2025 01:07 Berlin
    path = target_path(film, Naming("date", show="Werwölfe"))
    assert path == PurePosixPath("Werwoelfe/Werwoelfe - 2025-10-23 - Bonus - Lezan erklaert die Regeln.mp4")
    assert target_path(film, Naming("date", show="Werwölfe"), timezone=dt.UTC).name.startswith("Werwoelfe - 2025-10-22")
    assert target_path(film, Naming("date"), normalize=False).name.endswith("- Bonus - Lézan erklärt die Regeln.mp4")


@pytest.mark.parametrize(
    ("title", "expected"),
    [  # titles from the film list (13.10.2026)
        ("tagesschau 12:05 Uhr, 12.10.2026", "tagesschau 12-05 Uhr, 12.10.2026"),
        (
            "Spätes Gegentor: SV Elversberg verliert mit 0:1 bei Union Berlin",
            "Spaetes Gegentor - SV Elversberg verliert mit 0-1 bei Union Berlin",
        ),
        ("Österreichs Zeitzeug:innen", "Oesterreichs Zeitzeug-innen"),
        ("Folge 45:Ein Kapitän wird getauft", "Folge 45 - Ein Kapitaen wird getauft"),
        (
            'Ringer-Trainer Hartmann:" Deutsche Meisterschaften sind wichtig"',
            "Ringer-Trainer Hartmann - Deutsche Meisterschaften sind wichtig",
        ),
        ("Tarifabschluss:: Bahn einigt sich mit GdL", "Tarifabschluss - Bahn einigt sich mit GdL"),
    ],
)
def test_colons(title: str, expected: str) -> None:
    assert clean(title, normalize=True) == expected


def test_bonus_episodes_fall_back_to_date() -> None:
    # Was "S01E??" once: "?" is not allowed on SMB shares.
    film = werwoelfe("Bonus: Lézan erklärt die Regeln")
    path = target_path(film, Naming("season_episode", show="Werwölfe"))
    assert path == PurePosixPath("Werwoelfe/Werwoelfe - 2025-10-23 - Bonus - Lezan erklaert die Regeln.mp4")


def test_episode_number_without_number_falls_back_to_date() -> None:
    film = gipfeltreffen("Das Gipfeltreffen – Best of 2025")
    assert target_path(film, Naming("episode_number")).name == (
        "Das Gipfeltreffen - 2025-12-05 - Das Gipfeltreffen - Best of 2025.mp4"
    )


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        (
            "Nie wieder VAMPIRE DIARIES?! 😱 Rückblick, Vampir-Hype & ABGESETZT nach 13 Jahren",
            "Nie wieder VAMPIRE DIARIES! Rueckblick, Vampir-Hype & ABGESETZT nach 13 Jahren",
        ),
        ("37°: Meine ziemlich bunte Familie", "37 Grad - Meine ziemlich bunte Familie"),
        ("Stefan Waghubinger: Ich sag´s jetzt nur zu Ihnen", "Stefan Waghubinger - Ich sag's jetzt nur zu Ihnen"),
        ("The Garfield Show™", "The Garfield Show"),
        ("ERSTER TAG in HOGWARTS ⚡ HARRY POTTER QUIZ | Part 2", "ERSTER TAG in HOGWARTS HARRY POTTER QUIZ - Part 2"),
        (
            'Gipfeltreffen: "Putin weiß, dass Trump käuflich ist"',
            "Gipfeltreffen - Putin weiss, dass Trump kaeuflich ist",
        ),
        (
            "Johann König braucht das Gipfeltreffen nicht (S04/E28)",
            "Johann Koenig braucht das Gipfeltreffen nicht (S04-E28)",
        ),
        ("«ZAHLE»", "ZAHLE"),
        ("Z sanek powstają łyżwy", "Z sanek powstaja lyzwy"),
        ("Dźěło technikarjow/Technikcrew", "Dzelo technikarjow-Technikcrew"),
        ("Ein Sommer in …", "Ein Sommer in"),  # no dots at the end: Windows and SMB refuse them
        ("Спорт", "_"),
        ("Informant*in | Ilka Brecht", "Informantin - Ilka Brecht"),
        ("JURASSIC PARK > WORLD? Wir ranken ALLE Filme!", "JURASSIC PARK WORLD Wir ranken ALLE Filme!"),
    ],
)
def test_normalized_names(title: str, expected: str) -> None:
    assert clean(title, normalize=True) == expected


def test_forbidden_characters_go_also_without_normalizing() -> None:
    assert clean("Das F*** Wort", normalize=False) == "Das F Wort"
    assert clean("Dźěło technikarjow/Technikcrew", normalize=False) == "Dźěło technikarjow-Technikcrew"
    emoji = "ER hat bei „FIND THE PRO“ teilgenommen! 😳"
    assert clean(emoji, normalize=False) == emoji


def test_long_names_keep_marker_and_extension() -> None:
    film = gipfeltreffen("Das Beste kommt noch! (58)")
    long = dataclasses.replace(film, title="Sehr " * 80 + "lang (58)")
    path = target_path(long, Naming("episode_number"))
    assert len(path.name.encode()) <= 200
    assert path.name.startswith("Das Gipfeltreffen - S01E58 - Sehr Sehr")
    assert path.name.endswith(".mp4")


def test_templates() -> None:
    film = gipfeltreffen("Das Beste kommt noch! (58)")
    german = Naming("template", template="{thema}/{jahr}/{thema} - S{staffel}E{folge} - {titel} [{sender}].{ext}")
    assert target_path(film, german) == PurePosixPath(
        "Das Gipfeltreffen/2026/Das Gipfeltreffen - S01E58 - Das Beste kommt noch! [MDR].mp4"
    )
    english = Naming("template", template="{topic} - {date} - {title}")
    assert target_path(film, english) == PurePosixPath("Das Gipfeltreffen - 2026-08-20 - Das Beste kommt noch!.mp4")
    assert unknown_placeholders("{thema} {genre} {ext} {x}") == ["genre", "x"]


def test_livestream_without_date() -> None:
    film = dataclasses.replace(
        gipfeltreffen("Das Beste kommt noch! (58)"), title="3Sat Livestream", topic="Livestream", timestamp=None
    )
    assert target_path(film, Naming("date")) == PurePosixPath("Livestream/Livestream - 3Sat Livestream.mp4")
