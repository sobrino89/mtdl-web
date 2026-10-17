"""Broadcasts that are probably not in German, for the search filter "German only".

The film list has no language field. What it has are foreign editions among the channels and markers in titles and
topics. Film list of 17.10.2026, 721,176 entries: ARTE.FR, .ES, .PL, .EN and .IT carry 24,316. On the other channels:
"(Originalversion)" 3,166 (ARTE.DE 2,699, then ONE and ARD series), a language in brackets 1,780 times in SRF sport
clips ("(engl.)", "(frz.)") and 471 times as "(Englisch)" (ZDFneo series), "(OV)" 55, "(OmU)" 7, Sorbian 248
("Łužyca", "(sorbisch)"), another script 415 (ZDFarabic, Ukrainian "logo!" and "heute journal").

In all, 30,561 entries (4.2 %); samples of every kind checked by hand, the rarer markers one by one.

Left alone, because the sound is German or the word only names a language: "(mit Untertitel)" at ARTE (2,480 of 2,489
stand next to the same title without it), "(english subtitles)", "deutsch-französisch", "Englisch lernen", "antwortet
auf Englisch" in a children's series. Known false hit: a concert called "(Originalfassung)" for its score.
"""

import re

from mtdl_web.models import Film

# Channels that are foreign editions; ARTE.DE is the German one.
_EDITIONS = frozenset({"ARTE.FR", "ARTE.ES", "ARTE.PL", "ARTE.EN", "ARTE.IT"})

_LANGUAGES = (
    "englisch|english|engl|französisch|franz|frz|italienisch|ital|spanisch|span|portugiesisch|niederländisch|niederl"
    "|schwedisch|schwed|norwegisch|norw|dänisch|dän|finnisch|isländisch|polnisch|poln|tschechisch|ungarisch"
    "|griechisch|türkisch|türkçe|russisch|russ|ukrainisch|arabisch|arab|persisch|farsi|dari|paschtu|kurdisch"
    "|tigrinya|vietnamesisch|chinesisch|japanisch|koreanisch|hebräisch|romanes|sorbisch|rätoromanisch|rumantsch"
)
_MARKER = re.compile(
    r"\(\s*(?:OV|Om[de]?U)\b"  # (OV), (OmU), (OmeU), (OmU, 3/5)
    r"|\boriginal(?:version|fassung)\b"  # (Originalversion mit Untertitel), Originalfassung: …
    r"|\bim\s+\w+en\s+originalton\b"  # im englischen Originalton
    rf"|\(\s*(?:auf\s+)?(?:{_LANGUAGES})\.?\s*\)"  # (Englisch), (engl.), (sorbisch), (Türkçe)
    rf"|\(\s*(?:{_LANGUAGES})\w*\s+(?:fassung|version|originalfassung)\b"  # (englische Fassung)
    r"|\benglish\s+version\b|\ben\s+français\b|\bversion\s+française\b"
    rf"|^auf\s+(?:{_LANGUAGES})\s*:",  # Auf Russisch: …
    re.IGNORECASE,
)
# Two letters in a row of Cyrillic, Hebrew, Arabic, Thai, Japanese, Chinese or Korean script.
_SCRIPT = re.compile(
    r"[\u0400-\u04ff\u0590-\u05ff\u0600-\u06ff\u0750-\u077f\u0e00-\u0e7f\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]{2,}"
)
# The Sorbian magazines of RBB and MDR.
_SORBIAN = re.compile(r"(?:luzyca|łužyca|wuhladko)\b", re.IGNORECASE)


def other_language(film: Film) -> str | None:
    """Why ``film`` is probably not in German: the foreign edition ("ARTE.FR"), the marker as the film list writes it
    ("Originalversion", "engl.", "OV"), a word in another script, or the Sorbian magazine. None: German, or nothing
    says otherwise."""
    if film.channel in _EDITIONS:
        return film.channel
    for text in (film.title, film.topic):
        if found := _MARKER.search(text):
            return found.group(0).strip(" ():")
        if found := _SCRIPT.search(text):
            return found.group(0)
    if _SORBIAN.match(film.topic):
        return film.topic
    return None
