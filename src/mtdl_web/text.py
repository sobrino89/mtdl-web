"""Text normalization for comparisons.

Rules compare against one "comparison text", with and without regex: NFKC, every dash variant as "-", whitespace
(including no-break space) collapsed. Data behind this choice: docs/filmliste-format.md, section Normalisierung.
"""

import re
import unicodedata

_DASHES = str.maketrans(
    dict.fromkeys(
        "\N{HYPHEN}\N{NON-BREAKING HYPHEN}\N{FIGURE DASH}\N{EN DASH}\N{EM DASH}\N{HORIZONTAL BAR}"
        "\N{MINUS SIGN}\N{SMALL EM DASH}\N{SMALL HYPHEN-MINUS}",
        "-",
    )
)


def normalize(text: str) -> str:
    """Comparison text: case is kept, so regex matches can be shown in the original spelling."""
    return " ".join(unicodedata.normalize("NFKC", text).translate(_DASHES).split())


def fold(text: str) -> str:
    """Comparison text without case, for conditions without regex."""
    return normalize(text).casefold()


# Combining accents to drop after NFD: all of them, except the diaeresis on a, o and u (the German umlauts).
_ACCENTS = re.compile(f"(?<![aou]){chr(0x308)}|[{chr(0x300)}-{chr(0x307)}{chr(0x309)}-{chr(0x36F)}]")


_SHARP_S_WORD = re.compile(r"\w*ß\w*")


def search_text(text: str) -> str:
    """What a query searches for, lower case: accents go ("Café" is "cafe", "Israël" "israel", "İstanbul"
    "istanbul"), but "ä", "ö", "ü" and "ß" stay letters of their own. German needs them ("schon" is not "schön"),
    and the app is about German-language media libraries."""
    folded = text.lower()
    if folded.isascii():
        return folded
    return unicodedata.normalize("NFC", _ACCENTS.sub("", unicodedata.normalize("NFD", folded)))


def index_text(text: str) -> str:
    """What the full-text index stores: ``search_text``, and every word with "ß" a second and third time with "ss"
    and "s" ("Maß" → "maß mass mas"). So "Mass" and "Mas" find "Maß", while "Maß" finds only words with "ß", not
    "Masse". The index tokenizer itself removes nothing."""
    folded = search_text(text)
    if "ß" not in folded:
        return folded
    return _SHARP_S_WORD.sub(lambda m: f"{m[0]} {m[0].replace('ß', 'ss')} {m[0].replace('ß', 's')}", folded)
