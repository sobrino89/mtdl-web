"""Streaming reader for the MediathekView film list (format version 3).

The list is a single JSON object with duplicate keys: "Filmliste" twice (meta data, column names), then one "X" per
entry. JSON parsers keep only the last value of a duplicate key, so the object is read pair by pair with
``json.JSONDecoder.raw_decode``. Memory use stays constant; the full list (720k entries) takes about ten seconds.
"""

import contextlib
import datetime as dt
import json
import lzma
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO, TypeIs

from mtdl_web.filmlist.prepared import Fields, Prepared, prepare, stable_hash
from mtdl_web.models import Film, film_id

COLUMNS = (
    "Sender",
    "Thema",
    "Titel",
    "Datum",
    "Zeit",
    "Dauer",
    "Größe [MB]",
    "Beschreibung",
    "Url",
    "Website",
    "Url Untertitel",
    "Url RTMP",
    "Url Klein",
    "Url RTMP Klein",
    "Url HD",
    "Url RTMP HD",
    "DatumL",
    "Url History",
    "Geo",
    "neu",
)
(
    CHANNEL,
    TOPIC,
    TITLE,
    DATE,
    TIME,
    DURATION,
    SIZE_MB,
    DESCRIPTION,
    URL,
    WEBSITE,
    URL_SUBTITLE,
    URL_RTMP,
    URL_LOW,
    URL_RTMP_LOW,
    URL_HD,
    URL_RTMP_HD,
    TIMESTAMP,
    URL_HISTORY,
    GEO,
    NEW,
) = range(len(COLUMNS))

_CHUNK = 1 << 22
_MAX_VALUE = 1 << 24  # no single entry comes close; beyond this the input is broken, not truncated
_WHITESPACE = " \t\r\n"
_XZ_MAGIC = b"\xfd7zXZ\x00"
_decoder = json.JSONDecoder()


class FilmListError(ValueError):
    """The input is not a film list in the expected format."""


@dataclass(frozen=True, slots=True)
class ListMeta:
    created: dt.datetime  # UTC
    format_version: str
    crawler: str
    list_id: str


def _pairs(stream: TextIO) -> Iterator[tuple[object, object]]:
    """Yield the (key, value) pairs of the top-level object, keeping duplicate keys."""
    buf = stream.read(_CHUNK)
    pos = 0
    eof = not buf

    def more() -> bool:
        nonlocal buf, pos, eof
        if eof:
            return False
        data = stream.read(_CHUNK)
        if not data:
            eof = True
            return False
        buf = buf[pos:] + data
        pos = 0
        return True

    def peek() -> str:
        """Skip whitespace and return the next character, "" at the end of the input."""
        nonlocal pos
        while True:
            while pos < len(buf) and buf[pos] in _WHITESPACE:
                pos += 1
            if pos < len(buf):
                return buf[pos]
            if not more():
                return ""

    def value() -> object:
        nonlocal pos
        while True:
            try:
                val, pos = _decoder.raw_decode(buf, pos)
                return val
            except json.JSONDecodeError as exc:
                if len(buf) - pos > _MAX_VALUE or not more():
                    raise FilmListError(f"invalid JSON: {exc.msg}") from exc

    if peek() != "{":
        raise FilmListError("not a JSON object")
    pos += 1
    first = True
    while True:
        char = peek()
        if char == "}":
            return
        if not first:
            if char != ",":
                raise FilmListError("expected ',' or '}'" if char else "unexpected end of input")
            pos += 1
            peek()
        first = False
        key = value()
        if peek() != ":":
            raise FilmListError("expected ':'")
        pos += 1
        peek()
        yield key, value()


class FilmListReader:
    """Reads the meta data on construction; ``rows()`` or ``films()`` then stream the entries once."""

    def __init__(self, stream: TextIO) -> None:
        self._pairs = _pairs(stream)
        (key1, meta), (key2, columns) = next(self._pairs, (None, None)), next(self._pairs, (None, None))
        if key1 != "Filmliste" or key2 != "Filmliste":
            raise FilmListError("missing film list header")
        self.meta, *header = check_header(meta, columns)
        self.header = (header[0], header[1])  # raw, for tools that write film lists

    def rows(self) -> Iterator[list[str]]:
        """Raw entries with channel and topic filled in: an empty value means "same as the previous entry"."""
        channel = topic = ""
        for key, row in self._pairs:
            if key != "X":
                continue
            row = check_row(row)
            if row[CHANNEL]:
                channel = row[CHANNEL]
            else:
                row[CHANNEL] = channel
            if row[TOPIC]:
                topic = row[TOPIC]
            else:
                row[TOPIC] = topic
            yield row

    def films(self) -> Iterator[Film]:
        return map(to_film, self.rows())

    def prepared(self) -> Iterator[Prepared]:
        for row in self.rows():
            fields = convert_fields(row)
            yield prepare(row[CHANNEL], row[TOPIC], row[TITLE], row[URL], fields, stable_hash(fields))


@contextlib.contextmanager
def open_text(path: Path) -> Iterator[TextIO]:
    """A film list file as text, xz-compressed or plain."""
    with path.open("rb") as probe:
        compressed = probe.read(len(_XZ_MAGIC)) == _XZ_MAGIC
    if compressed:
        with lzma.open(path, "rt", encoding="utf-8") as xz:
            yield xz
    else:
        with path.open(encoding="utf-8") as plain:
            yield plain


@contextlib.contextmanager
def open_filmlist(path: Path) -> Iterator[FilmListReader]:
    with open_text(path) as text:
        yield FilmListReader(text)


def _strings(value: object) -> TypeIs[list[str]]:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def check_header(meta: object, columns: object) -> tuple[ListMeta, list[str], list[str]]:
    """Validate the two "Filmliste" values; meta is [created local "dd.mm.yyyy, HH:MM", created UTC, format
    version, crawler, list id]."""
    if not _strings(meta) or not _strings(columns):
        raise FilmListError("missing film list header")
    if tuple(columns[: len(COLUMNS)]) != COLUMNS:
        raise FilmListError(f"unexpected columns: {columns!r}")
    if len(meta) < 5:
        raise FilmListError(f"unexpected meta data: {meta!r}")
    try:
        created = dt.datetime.strptime(meta[1], "%d.%m.%Y, %H:%M").replace(tzinfo=dt.UTC)
    except ValueError as exc:
        raise FilmListError(f"unexpected creation time: {meta[1]!r}") from exc
    return ListMeta(created=created, format_version=meta[2], crawler=meta[3], list_id=meta[4]), meta, columns


def check_row(row: object) -> list[str]:
    if not _strings(row) or len(row) < len(COLUMNS):
        raise FilmListError(f"malformed entry: {row!r:.200}")
    return row


_RELATIVE = re.compile(r"(\d+)\|")
_VALID_URL = re.compile(r"https?://[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?::\d+)?/")
_DOUBLE_SCHEME = re.compile(r"https?://[A-Za-z0-9.-]+(https?://.+)")
_SCHEMELESS = re.compile(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+/")


def resolve_relative(base: str, value: str) -> str:
    """Low and HD urls are stored as "n|suffix": the first n characters of the main url, then the suffix."""
    m = _RELATIVE.match(value)
    return base[: int(m[1])] + value[m.end() :] if m else value


def clean_url(value: str) -> str | None:
    """Repair the known kinds of damage, None if the url stays unusable."""
    url = value.strip()
    if not url:
        return None
    if m := _DOUBLE_SCHEME.fullmatch(url):  # "http://www.ardmediathek.dehttps://www.ardmediathek.de/subtitle/…"
        url = m[1]
    elif url.startswith("//"):  # protocol-relative (SR)
        url = "https:" + url
    elif _SCHEMELESS.match(url):  # "subtitles.swr.de/swr/…"
        url = "https://" + url
    return url if _VALID_URL.match(url) else None


def _seconds(hms: str) -> int | None:
    parts = hms.split(":")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    hours, minutes, seconds = map(int, parts)
    return hours * 3600 + minutes * 60 + seconds


def _int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def convert_fields(row: list[str]) -> Fields:
    url = row[URL]
    size_mb = _int(row[SIZE_MB])
    return (
        row[DESCRIPTION],
        _int(row[TIMESTAMP]),
        _seconds(row[DURATION]),
        None if size_mb is None else size_mb * 2**20,  # the list counts MiB
        clean_url(url),
        clean_url(resolve_relative(url, row[URL_LOW])),
        clean_url(resolve_relative(url, row[URL_HD])),
        clean_url(row[URL_SUBTITLE] or row[URL_RTMP]),  # KiKA puts its subtitle urls into the obsolete RTMP column
        row[WEBSITE].strip() or None,
        row[GEO],
    )


def to_film(row: list[str]) -> Film:
    """Convert a raw entry (channel and topic already filled in) into the shared model."""
    description, timestamp, duration, size, url, low, hd, subtitle, website, geo = convert_fields(row)
    return Film(
        id=film_id(row[CHANNEL], row[TOPIC], row[TITLE], row[URL]),
        channel=row[CHANNEL],
        topic=row[TOPIC],
        title=row[TITLE],
        description=description,
        timestamp=timestamp,
        duration=duration,
        size=size,
        url=url,
        url_low=low,
        url_hd=hd,
        url_subtitle=subtitle,
        url_website=website,
        geo=tuple(geo.split("-")) if geo else (),
    )
