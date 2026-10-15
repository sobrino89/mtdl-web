"""Episode markers in the titles of a film list: which forms the broadcasters use, how often, and how many of them
library.marker() recognises today. Produces the tables of docs/folgenerkennung.md.

    python tools/folgenmarken.py <films.sqlite>

The film index is the one mtdl-web keeps in its cache folder (/config/cache/films.sqlite); a copy is enough, it is
opened read-only. Counts are per title; a title can show up in several rows. Runs in the test image:

    docker run --rm -v /pfad/zu/films.sqlite:/data/films.sqlite:ro mtdl-web:test \\
        python tools/folgenmarken.py /data/films.sqlite
"""

import collections
import re
import sqlite3
import sys
from dataclasses import dataclass, field

from mtdl_web.library import marker


@dataclass
class Form:
    label: str  # as the table shows it
    pattern: re.Pattern[str]
    channel: str | None = None  # only titles of this channel
    hits: list[tuple[str, str, str]] = field(default_factory=list)  # channel, topic, title


def form(label: str, pattern: str, channel: str | None = None) -> Form:
    return Form(label, re.compile(pattern), channel)


SE = r"\(S\d{1,4}/E\d{1,4}\)"
# Versions of an episode after its marker: audio description, sign language, language, original version.
VERSION = (
    r"\s*(?:\((?:Audiodeskription|Gebärdensprache|Englisch|Französisch|Originalversion|OV|[a-z]{3})\)"
    r"|- Audiodeskription)"
)

GROUPS: dict[str, list[Form]] = {
    "Erkennt mtdl-web heute (Marke am Titelende)": [
        form("(S02/E03)", r"\(S\d{1,2}/E\d{1,4}\)\s*$"),
        form("(S2026/E08), Staffel als Jahr", r"\(S(?:19|20)\d{2}/E\d{1,4}\)\s*$"),
        form("(Staffel 2, Folge 3)", r"\(Staffel \d{1,4}, Folge \d{1,4}\)\s*$"),
        form("(58), fortlaufend", r"\((?!(?:19|20)\d{2}\))\d{1,4}\)\s*$"),
    ],
    "Folgenmarken, die mtdl-web heute nicht erkennt": [
        form("(S01/E06) vor einer Fassungsangabe", SE + r"(?:" + VERSION + r")+\s*$"),
        form("(S2024E81), ohne Schrägstrich", r"\(S\d{1,4} ?E\d{1,4}\)"),
        form("S2026 E6, S1E5 ohne Klammer", r"(?<![A-Za-z0-9(])S\d{1,4} ?E\d{1,4}(?![0-9)])"),
        form("(Folge 5)", r"\(Folge \d{1,4}\)"),
        form("(F. 328)", r"\(F\. ?\d{1,4}\)"),
        form("(E68)", r"\(E\d{1,3}\)"),
        form("(Teil 2)", r"\(Teil \d{1,3}\)"),
        form("(1/4), Teil 1 von 4", r"\((?:Teil |Folge )?\d{1,3} ?(?:/|v) ?\d{1,3}\)"),
        form("(Staffel 2, 3/6)", r"\(Staffel \d{1,4}, (?:Folge )?\d{1,4}/\d{1,4}\)"),
        form("Staffel 4, Folge 3 im Titel", r"Staffel \d{1,4},? Folge \d{1,4}(?![\d)])"),
        form("Folge 1311: am Titelanfang", r"^\s*Folge\s+\d{1,4}\s*[:.\-–]"),
        form("27. Der Taschengeldtrick (KiKA)", r"^\s*\d{1,3}\.\s", channel="KiKA"),
        form("Episode 656", r"\bEpisode\s+\d{1,4}\b"),
    ],
    "Zahlen, die keine Folge meinen": [
        form("(2024), ein Jahr", r"\((?:19|20)\d{2}\)\s*$"),
        form("(14.03.2003), Datum", r"\(\d{1,2}\.\d{1,2}\.\d{2,4}\)"),
        form("(23/10/2020), Datum (ARTE.FR)", r"\(\d{1,2}/\d{1,2}/\d{4}\)"),
        form("(Clip 31)", r"\(Clip \d{1,4}\)"),
        form("(Staffel 2), nur Staffel", r"\(Staffel \d{1,4}\)"),
        form("(S2022), nur Staffel", r"\(S\d{1,4}\)"),
        form("(1933-1935), Zeitraum", r"\((?:19|20|18)\d{2}\s?(?:-|–|bis)\s?(?:19|20|18)\d{2}\)"),
    ],
}


def main(path: str) -> None:
    con = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    rows: list[tuple[str, str, str]] = con.execute("SELECT channel, topic, title FROM film").fetchall()
    recognised = sum(1 for _, _, title in rows if marker(title)[0] is not None)
    print(f"{len(rows)} Titel, {len({(c, t) for c, t, _ in rows})} Themen; marker() erkennt {recognised}.\n")
    for forms in GROUPS.values():
        for f in forms:
            f.hits = [r for r in rows if (f.channel is None or r[0] == f.channel) and f.pattern.search(r[2])]
    for heading, forms in GROUPS.items():
        print(f"### {heading}\n")
        print("| Form | Titel | Themen | Sender (häufigste) | davon erkannt | Beispiel |")
        print("|---|---:|---:|---|---:|---|")
        for f in sorted(forms, key=lambda f: -len(f.hits)):
            topics = len({(c, t) for c, t, _ in f.hits})
            channels = ", ".join(f"{c} {n}" for c, n in collections.Counter(c for c, _, _ in f.hits).most_common(3))
            known = sum(1 for _, _, title in f.hits if marker(title)[0] is not None)
            example = f"{f.hits[0][0]}: {f.hits[0][2]}" if f.hits else ""
            example = example.replace("|", "/")
            print(f"| {f.label} | {len(f.hits)} | {topics} | {channels} | {known} | {example[:110]} |")
        print()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
