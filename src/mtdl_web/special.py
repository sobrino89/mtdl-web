"""Special versions of a broadcast: audio description, sign language, "Hörfassung", "klare Sprache" and the
version with subtitles that ARTE lists next to the plain one. Subscriptions leave them out unless told otherwise: the
setting ``exclude_special_versions`` (on by default), overridden per subscription with "exclude" or "include".

Matched are the markers as the film list writes them, in the title or the topic. Film list of 15.10.2026: of 30,110
titles that name one of the words, 29,956 are versions, and 8,378 topics such as "Tagesschau in Gebärdensprache".
Forms: "(Audiodeskription)", "(mit Gebärdensprache)", "(Video in klarer Sprache)", "mit Deutscher Gebärdensprache",
"in Gebärdensprache", "| Audiodeskription |", "- Audiodeskription", "Gebärdensprache: …", and a topic that is only
the word. Left alone are programmes about the subject: "Was ist Gebärdensprache?", "Geschichte der Deutschen
Gebärdensprache", "Ersatz für das Hören – Gebärdensprache und Implantat".

"(mit Untertitel)", film list of 18.10.2026: 2,504 at ARTE.DE, 2,480 of them next to the same title without it, 4,115
at ARTE.FR, one at SRF. Not "(Originalversion mit Untertitel)": that is the original sound (language.py).
"""

import re
from typing import Literal

from mtdl_web.models import Film

SpecialVersions = Literal["setting", "exclude", "include"]

_WORD = r"(?:audiodeskription|hörfassung|gebärdensprache|klare[rn]?\s+sprache)"
_MARKER = re.compile(
    rf"\(\s*(?:mit\s+|video\s+in\s+)?{_WORD}\s*\)"
    rf"|\b(?:mit|in)\s+(?:deutscher\s+)?{_WORD}"
    rf"|(?:^|[-–|])\s*{_WORD}\s*(?:[-–|:]|$)"
    r"|\(\s*mit\s+untertiteln?\s*\)",
    re.IGNORECASE,
)


def special_version(film: Film) -> str | None:
    """The marker that makes ``film`` a special version ("Audiodeskription", "mit Gebärdensprache"), else None."""
    for text in (film.title, film.topic):
        if found := _MARKER.search(text):
            return found.group(0).strip(" ()-–|:")
    return None


def excluded(choice: SpecialVersions, setting: bool) -> bool:
    """Whether a subscription with ``choice`` leaves special versions out, the setting being ``setting``."""
    return choice == "exclude" or (choice == "setting" and setting)
