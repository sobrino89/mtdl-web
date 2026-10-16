"""Subtitles to SRT. The format is recognised by content, not by the url (docs/filmliste-format.md):

- TTML / EBU-TT-D (ARD, ZDF, KiKA, SR with prefix ``tt:``; SRF, ORF without), times as clock time, frames,
  seconds, milliseconds or ticks; SR starts at the broadcast time code 10:00:00.
- WebVTT (older SRF entries), with ``<font>`` and other tags.

Anything else (an HTML page at NDR, error pages) yields None; the film is still downloaded.
"""

import html
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

_CLOCK = re.compile(r"^(\d+):(\d{2}):(\d{2})(?:([.:])(\d+))?$")
_OFFSET = re.compile(r"^(\d+(?:\.\d+)?)(h|m|s|ms|f|t)$")
_VTT_TIME = re.compile(r"^(?:(\d+):)?(\d{2}):(\d{2})\.(\d{3})$")
_TAG = re.compile(r"<[^>]+>")
_ESCAPED_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)
_SPACE = re.compile(r"[ \t\r\n]+")
TTP = "{http://www.w3.org/ns/ttml#parameter}"


@dataclass(frozen=True, slots=True)
class Cue:
    start: float  # seconds
    end: float
    text: str  # lines separated by "\n"


def detect(data: bytes) -> str | None:
    head = data[:4096].lstrip(b"\xef\xbb\xbf \t\r\n")
    if head.startswith(b"WEBVTT"):
        return "webvtt"
    if b"http://www.w3.org/ns/ttml" in head or re.search(rb"<(\w+:)?tt[\s>]", head):
        return "ttml"
    return None


def _ttml_time(value: str, frame_rate: float, tick_rate: float) -> float | None:
    value = value.strip()
    if m := _CLOCK.match(value):
        hours, minutes, seconds, sep, fraction = m.groups()
        total = float(int(hours) * 3600 + int(minutes) * 60 + int(seconds))
        if sep == ".":
            total += float("0." + fraction)
        elif sep == ":":
            total += int(fraction) / frame_rate
        return float(total)
    if m := _OFFSET.match(value):
        number, unit = float(m[1]), m[2]
        factor = {"h": 3600, "m": 60, "s": 1, "ms": 0.001}.get(unit)
        if factor is not None:
            return number * factor
        return number / (frame_rate if unit == "f" else tick_rate)
    return None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _ttml_text(p: ET.Element) -> str:
    parts: list[str] = []

    def walk(element: ET.Element) -> None:
        if element is not p and _local(element.tag) == "br":
            parts.append("\n")
        elif element.text:
            parts.append(element.text)
        for child in element:
            walk(child)
            if child.tail:
                parts.append(child.tail)

    walk(p)
    text = _ESCAPED_BR.sub("\n", "".join(parts))  # ORF writes "&lt;br/&gt;" into the text
    lines = (_SPACE.sub(" ", line).strip() for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


def _rate(value: str | None, default: float) -> float:
    """A frame or tick rate from the file; a missing, broken or non-positive one gives way to the default."""
    try:
        rate = float(value) if value else default
    except ValueError:
        return default
    return rate if math.isfinite(rate) and rate > 0 else default


def parse_ttml(data: bytes) -> list[Cue]:
    root = ET.fromstring(data)
    frame_rate = _rate(root.get(f"{TTP}frameRate"), 30.0)
    tick_rate = _rate(root.get(f"{TTP}tickRate"), frame_rate)
    cues = []
    for p in root.iter():
        if _local(p.tag) != "p":
            continue
        begin = _ttml_time(p.get("begin", ""), frame_rate, tick_rate)
        end = _ttml_time(p.get("end", ""), frame_rate, tick_rate)
        if end is None and begin is not None:
            duration = _ttml_time(p.get("dur", ""), frame_rate, tick_rate)
            end = begin + duration if duration is not None else None
        text = _ttml_text(p)
        if begin is not None and end is not None and text:
            cues.append(Cue(begin, end, text))
    return cues


def _vtt_time(value: str) -> float | None:
    m = _VTT_TIME.match(value.strip())
    if not m:
        return None
    hours, minutes, seconds, millis = m.groups()
    return int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000


def parse_webvtt(text: str) -> list[Cue]:
    cues = []
    for block in re.split(r"\r?\n\s*\r?\n", text.replace("\r\n", "\n")):
        lines = [line for line in block.split("\n") if line.strip()]
        timing = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing is None:
            continue  # header, NOTE, STYLE, REGION
        start_text, _, rest = lines[timing].partition("-->")
        start, end = _vtt_time(start_text), _vtt_time(rest.split()[0] if rest.split() else "")
        body = [html.unescape(_TAG.sub("", line)).strip() for line in lines[timing + 1 :]]
        body = [line for line in body if line]
        if start is not None and end is not None and body:
            cues.append(Cue(start, end, "\n".join(body)))
    return cues


def remove_time_code_offset(cues: list[Cue], duration: int | None) -> list[Cue]:
    """Subtitles timed against the broadcast time code (SR: 10:00:00) start hours after the film: move them back
    by whole hours when the first one would start after the end of the film (or at 10 h and later, length unknown)."""
    if not cues:
        return cues
    first = min(c.start for c in cues)
    behind = first > duration + 60 if duration is not None else first >= 10 * 3600
    if first < 3600 or not behind:
        return cues
    shift = int(first // 3600) * 3600
    return [Cue(c.start - shift, c.end - shift, c.text) for c in cues]


def _srt_time(seconds: float) -> str:
    millis = max(0, round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def to_srt(cues: list[Cue]) -> str:
    blocks = []
    for index, cue in enumerate(sorted(cues, key=lambda c: (c.start, c.end)), 1):
        blocks.append(f"{index}\n{_srt_time(cue.start)} --> {_srt_time(cue.end)}\n{cue.text}\n")
    return "\n".join(blocks)


def convert(data: bytes, duration: int | None = None) -> str | None:
    """SRT text, or None if ``data`` is no subtitle format we know."""
    kind = detect(data)
    try:
        if kind == "ttml":
            cues = parse_ttml(data)
        elif kind == "webvtt":
            cues = parse_webvtt(data.decode("utf-8-sig", errors="replace"))
        else:
            return None
        cues = remove_time_code_offset(cues, duration)
        return to_srt(cues) if cues else None
    except (ET.ParseError, ValueError, ArithmeticError):  # a broken file is no subtitle, not a failed download
        return None
