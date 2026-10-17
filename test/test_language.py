"""Broadcasts probably not in German (language.py), for the search filter "German only". Channels, topics and titles
are real, from the film list of 17.10.2026."""

import pytest

from mtdl_web.language import other_language
from mtdl_web.models import Film

OTHER = [  # (channel, topic, title) -> what gives it away
    ("ARTE.FR", "Culture et pop - Culture pop", "Karambolage España - les croquettes", "ARTE.FR"),
    (
        "ARTE.FR",
        "Voyages et découvertes - A table !",
        "Le tafelspitz - A vos marmites ! - La recette (mit Untertitel)",
        "ARTE.FR",
    ),
    ("ARTE.DE", "Kino - Kurzfilme", "Clot (Originalversion mit Untertitel)", "Originalversion"),
    (
        "ONE",
        "Sister Boniface Mysteries",
        "Folge 7: Die glücklichste Familie (S04/E07) (Originalversion)",
        "Originalversion",
    ),
    (
        "ARD",
        "Money Maker",
        "Originalfassung: Tuyen Pham - vom Flüchtling zum Millionär (Vietnamesisch)",
        "Originalfassung",
    ),
    ("ARD", "Kommissar Wisting", "Eisige Schatten - Folge 1 (S01/E01) (OV)", "OV"),
    ("RBB", "Arthur & Diana (OmU)", "Arthur & Diana (OmU)", "OmU"),
    ("PHOENIX", "Dokumentationen", "Sister of Mine (OmeU)", "OmeU"),
    ("ZDF", "Springflut", 'Kriminelle Machenschaften (OmU, 3/5) -  Nach dem Roman "Die dritte Stimme"', "OmU"),
    ("SRF", "Sport-Clip", "Pogacar: «Ich hatte gute Beine – es war eine Traumetappe» (engl.)", "engl."),
    ("SRF", "Sport-Clip", "Jan Cadieux über die Ziele in dieser Saison (frz.)", "frz."),
    ("ZDFneo", "The Rookie", "Nachtschatten (S02/E02) (Englisch)", "Englisch"),
    ("3Sat", "Spielfilm", "Bleib bei uns - Spielfilm, Frankreich 2022 (Französisch)", "Französisch"),
    ("ARD", "Die Maus", "Marienkäfer (ukrainisch) (mit Gebärdensprache)", "ukrainisch"),
    ("ARD", "Die Sendung mit dem Elefanten", "Die Sendung mit dem Elefanten - Judo (Dari)", "Dari"),
    ("WDR", "Erdoğan (Türkçe)", "Bölüm 3: Gücün girdabında (türkisch)", "türkisch"),
    ("3Sat", "Dokumentarfilm", "Deutschlandlieder - Almanya Türküleri (Türkische Fassung)", "Türkische Fassung"),
    (
        "ARD",
        "Inside the Game",
        "Inside the Game -  Cyberpunk 2077: Phantom Liberty (English Version)",
        "English Version",
    ),
    ("SRF", "Helveticus - en français", "Alberto Giacometti (Staffel 1, Folge 23)", "en français"),
    ("SRF", "Paris 2024 Clips", "Bekeles Statement: Zufrieden, aber sie hatte zu kämpfen (auf franz.)", "auf franz."),
    ("NDR", "einfach.Medien", "Auf Russisch: Ist im Journalismus alles erlaubt?", "Auf Russisch"),
    ("ZDF", "PUR+", "Save corals - now! - Im englischen Originalton", "Im englischen Originalton"),
    ("ZDF", "heute journal", "heute journal, 18 травня 2022 (українською)", "травня"),
    ("ZDF", "ZDFarabic", "من لاجئة إلى ناشطة في مجال حقوق الإنسان", "من"),
    ("RBB", "Luzyca", "Wólby za Zwězkowy sejm / Bundestagswahl und Minderheiten", "Luzyca"),
    ("MDR", "Wuhladko", "Wosebitaj dwójnikaj/Besondere Zwillinge", "Wuhladko"),
]
GERMAN = [  # German sound, or the language is only named
    ("ARTE.DE", "Aktuelles und Gesellschaft - Reportagen", "Re: Grüner Güterverkehr (mit Untertitel)"),
    ("RBB", "Charité intensiv: Station 43", "Chapter 1: DYING (english subtitles)"),
    ("ARD", "aktueller bericht", 'Deutsch-französisches Festival "Loostik"'),
    ("ARD", "UNSERDING", "Warum ist Französisch so wichtig?"),
    ("ZDF", "Mako", "Alex antwortet auf Englisch"),
    ("BR", "BR Retro", "Arabisch-jüdisches Zusammenleben in Israel 1963"),
    ("PHOENIX", "phoenix nachgefragt", "mit Nadine Lindner (Deutschlandfunk)"),
    ("DW", "DW Nachrichten", "Heusgen: Kurs der USA gegenüber Russland ist ein Fehler"),
    ("ZDFneo", "A Good Girl's Guide to Murder", "Gutes Mädchen, böses Blut (S02/E01)"),
    ("ZDF", "37 Grad", "37°: Klicks um jeden Preis (Audiodeskription)"),  # a special version, but German
]


def film(channel: str, topic: str, title: str) -> Film:
    return Film(1, channel, topic, title, "", None, None, None, None, None, None, None, None, ())


@pytest.mark.parametrize(("channel", "topic", "title", "why"), OTHER)
def test_markers_of_another_language(channel: str, topic: str, title: str, why: str) -> None:
    assert other_language(film(channel, topic, title)) == why


@pytest.mark.parametrize(("channel", "topic", "title"), GERMAN)
def test_german_sound_and_mere_mentions_stay(channel: str, topic: str, title: str) -> None:
    assert other_language(film(channel, topic, title)) is None
