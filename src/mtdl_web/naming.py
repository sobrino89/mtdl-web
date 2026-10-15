"""File names for downloads:

- ``episode_number``: number from the title, "(39)" → ``Show/Season 01/Show - S01E39 - Title.mp4``
- ``season_episode``: season and episode from the title, "(S02/E03)" → ``Show/Season 02/Show - S02E03 - Title.mp4``
- ``date``: ``Show/Show - 2026-08-20 - Title.mp4``
- ``template``: own pattern with placeholders, German or English: {thema|topic} {titel|title} {staffel|season}
  {folge|episode} {datum|date} {jahr|year} {sender|channel} {ext}

Characters that file systems forbid are always replaced. With ``normalize`` (setting, default on) names become
plain ASCII: umlauts transliterated (ä → ae), other accents dropped, emojis and other symbols removed. Real
titles behind the rules: docs/filmliste-format.md and test/test_naming.py.
"""

import datetime as dt
import re
import unicodedata
import zoneinfo
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

from mtdl_web.models import Film

Scheme = Literal["episode_number", "season_episode", "date", "template"]

_EPISODE = re.compile(r"\s*\((\d+)\)$")
_SEASON_EPISODE = re.compile(r"\s*\(S(\d+)/E(\d+)\)$")
_PLACEHOLDER = re.compile(r"\{(\w+)\}")
BERLIN = zoneinfo.ZoneInfo("Europe/Berlin")
_MAX_BYTES = 200  # per path component: room for ".part", ".de.srt" and conservative file systems

# Always: what file systems or SMB shares refuse, and control characters (colons: see _colon).
_FORBIDDEN = str.maketrans(
    {"/": "-", "\\": "-", "|": " - ", "*": None, "?": None, '"': None, "<": None, ">": None}
    | {chr(c): None for c in range(32)}
    | {"\x7f": None}
)
# With normalize: German transliteration first, then letters without a decomposition, then typography.
_GERMAN = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss", "ẞ": "SS"})
_LETTERS = str.maketrans(
    {"ł": "l", "Ł": "L", "đ": "d", "Đ": "D", "ø": "o", "Ø": "O", "æ": "ae", "Æ": "Ae", "œ": "oe", "Œ": "Oe"}
    | {"ı": "i", "þ": "th", "Þ": "Th", "ð": "d", "Ð": "D"}
)
_TYPOGRAPHY = str.maketrans(
    dict.fromkeys(
        "\N{EN DASH}\N{EM DASH}\N{HYPHEN}\N{NON-BREAKING HYPHEN}\N{MINUS SIGN}"
        "\N{MIDDLE DOT}\N{BULLET OPERATOR}\N{BULLET}",
        "-",
    )
    | dict.fromkeys(
        "\N{RIGHT SINGLE QUOTATION MARK}\N{LEFT SINGLE QUOTATION MARK}\N{ACUTE ACCENT}\N{GRAVE ACCENT}", "'"
    )
    | dict.fromkeys(
        "\N{LEFT-POINTING DOUBLE ANGLE QUOTATION MARK}\N{RIGHT-POINTING DOUBLE ANGLE QUOTATION MARK}"
        "\N{DOUBLE LOW-9 QUOTATION MARK}\N{LEFT DOUBLE QUOTATION MARK}\N{RIGHT DOUBLE QUOTATION MARK}"
        "\N{SINGLE LOW-9 QUOTATION MARK}\N{SINGLE LEFT-POINTING ANGLE QUOTATION MARK}"
        "\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK}"
    )
    | {"\N{HORIZONTAL ELLIPSIS}": "...", "\N{DEGREE SIGN}": " Grad", "\N{NO-BREAK SPACE}": " "}
    # NFKD would spell these out ("™" → "TM", "½" → "1⁄2" → "12"): handle them before.
    | dict.fromkeys("\N{TRADE MARK SIGN}\N{REGISTERED SIGN}\N{COPYRIGHT SIGN}\N{SERVICE MARK}")
    | {"\N{FRACTION SLASH}": "-", "\N{VULGAR FRACTION ONE HALF}": "1-2", "\N{VULGAR FRACTION ONE QUARTER}": "1-4"}
)


@dataclass(frozen=True, slots=True)
class Episode:
    season: int | None
    number: int | None
    title: str  # without the episode marker


def episode(title: str) -> Episode:
    if m := _SEASON_EPISODE.search(title):
        return Episode(int(m[1]), int(m[2]), title[: m.start()])
    if m := _EPISODE.search(title):
        return Episode(None, int(m[1]), title[: m.start()])
    return Episode(None, None, title)


Target = Literal["downloads", "library"]


@dataclass(frozen=True, slots=True)
class Naming:
    scheme: Scheme = "date"
    show: str | None = None  # name of the series, default: the film's topic
    folder: str | None = None  # default: the show
    season: int | None = None  # for episode_number; default 1
    template: str | None = None  # for scheme "template"; path parts separated by "/"
    # "library" loads into a series library (``library_id``, libraries.py) under TMDB's names and numbers
    # (library.py); the fields above then do not apply. ``tmdb_id`` is the series the subscription belongs to.
    target: Target = "downloads"
    tmdb_id: int | None = None
    library_id: int | None = None


def _colon(m: re.Match[str]) -> str:
    """SMB shares refuse colons: "19:00" and "Zuschauer:innen" get a hyphen, everything else a dash with spaces
    ("Titel: Untertitel", "Folge 45:Ein Kapitän"). Checked old against new on the whole film list, 13.10.2026."""
    text = m.string
    before, after = text[m.start() - 1 : m.start()], text[m.end() : m.end() + 1]
    if (before.isdigit() and after.isdigit()) or (before.islower() and after.islower()):
        return "-"
    return " - "


def clean(text: str, *, normalize: bool) -> str:
    """One path component. Never empty, never only dots."""
    if normalize:
        text = text.translate(_GERMAN).translate(_LETTERS).translate(_TYPOGRAPHY)
        text = unicodedata.normalize("NFKD", text)
        text = "".join(c for c in text if not unicodedata.combining(c))
        text = text.encode("ascii", "ignore").decode()
    text = re.sub(":", _colon, text).translate(_FORBIDDEN)
    text = re.sub(r"\s+", " ", text).strip(" .")
    text = re.sub(r"(\s*-)+\s*-", " -", text)  # "a - - b" after removed characters
    return text.strip(" .-") or "_"


def _fit(name: str, keep_end: str) -> str:
    """Shorten ``name`` to the byte limit, cutting before ``keep_end`` (extension, episode marker)."""
    if len((name + keep_end).encode()) <= _MAX_BYTES:
        return name + keep_end
    budget = _MAX_BYTES - len(keep_end.encode())
    cut = name.encode()[:budget].decode(errors="ignore").rstrip(" .-")
    return cut + keep_end


def target_path(
    film: Film,
    naming: Naming,
    *,
    normalize: bool = True,
    timezone: dt.tzinfo = BERLIN,
    ext: str = "mp4",
) -> PurePosixPath:
    """Path relative to the download folder."""
    info = episode(film.title)
    show = clean(naming.show or film.topic, normalize=normalize)
    folder = clean(naming.folder, normalize=normalize) if naming.folder else show
    day = dt.datetime.fromtimestamp(film.timestamp, timezone).date().isoformat() if film.timestamp is not None else None
    if naming.scheme == "template" and naming.template:
        return _from_template(film, naming, info, day, ext, normalize)
    if naming.scheme == "date" or info.number is None:  # bonus, trailer: no episode to number
        title = clean(film.title, normalize=normalize)
        prefix = f"{show} - {day} - " if day else f"{show} - "
        return PurePosixPath(folder, _fit(prefix + title, f".{ext}"))
    season = (info.season if naming.scheme == "season_episode" else None) or naming.season or 1
    marker = f"S{season:02d}E{info.number:02d}"
    title = clean(info.title, normalize=normalize)
    name = _fit(f"{show} - {marker} - {title}", f".{ext}")
    if not name.startswith(f"{show} - {marker}"):
        name = _fit(f"{show} - {marker}", f".{ext}")  # the show name alone uses up the limit
    return PurePosixPath(folder, f"Season {season:02d}", name)


def _from_template(
    film: Film, naming: Naming, info: Episode, day: str | None, ext: str, normalize: bool
) -> PurePosixPath:
    season = info.season or naming.season or 1
    values = {
        "topic": naming.show or film.topic,
        "title": info.title,
        "season": f"{season:02d}",
        "episode": f"{info.number:02d}" if info.number is not None else "??",
        "date": day or "",
        "year": day[:4] if day else "",
        "channel": film.channel,
        "ext": ext,
    }
    aliases = {"thema": "topic", "titel": "title", "staffel": "season", "folge": "episode", "datum": "date"}
    aliases |= {"jahr": "year", "sender": "channel"}
    assert naming.template is not None
    return render_template(naming.template, values, aliases, normalize=normalize, ext=ext)


def render_template(
    template: str, values: dict[str, str], aliases: dict[str, str], *, normalize: bool, ext: str
) -> PurePosixPath:
    """Fills ``{placeholders}`` part by part ("/" separates folders); unknown ones stay as written."""

    def value(m: re.Match[str]) -> str:
        key = aliases.get(m[1], m[1])
        return values.get(key, m[0])

    parts = [p for p in template.split("/") if p.strip()]
    out = []
    for i, part in enumerate(parts):
        text = _PLACEHOLDER.sub(value, part)
        if i == len(parts) - 1:
            stem, dot, extension = text.rpartition(".")
            if dot and extension == ext:
                out.append(_fit(clean(stem, normalize=normalize), f".{ext}"))
                continue
            out.append(_fit(clean(text, normalize=normalize), f".{ext}"))
        else:
            out.append(_fit(clean(text, normalize=normalize), ""))
    return PurePosixPath(*out)


def unknown_placeholders(template: str) -> list[str]:
    known = {"topic", "title", "season", "episode", "date", "year", "channel", "ext"}
    known |= {"thema", "titel", "staffel", "folge", "datum", "jahr", "sender"}
    return [m[1] for m in _PLACEHOLDER.finditer(template) if m[1] not in known]
