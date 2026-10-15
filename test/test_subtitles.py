from pathlib import Path

import pytest

from mtdl_web.downloads.subtitles import Cue, convert, detect, parse_ttml, parse_webvtt, remove_time_code_offset

# Real subtitles of 12.10.2026, shortened to their first 12 cues (test/fixtures/README.md).
SUBTITLES = Path(__file__).parent / "fixtures" / "subtitles"


def cues(name: str) -> list[Cue]:
    data = (SUBTITLES / name).read_bytes()
    return parse_webvtt(data.decode()) if name.endswith(".vtt") else parse_ttml(data)


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("ard-ebutt-mdr.xml", "ttml"),
        ("zdf-ebutt.xml", "ttml"),
        ("kika-ebutt.xml", "ttml"),
        ("sr-ebutt-timecode.xml", "ttml"),
        ("srf-ttml.xml", "ttml"),
        ("orf-ttml.ttml", "ttml"),
        ("srf.vtt", "webvtt"),
        ("ndr-not-subtitles.html", None),
    ],
)
def test_format_is_recognised_by_content(name: str, kind: str | None) -> None:
    assert detect((SUBTITLES / name).read_bytes()) == kind


def test_ebu_tt_with_line_breaks_and_pretty_printing() -> None:
    first, second = cues("ard-ebutt-mdr.xml")[:2]
    assert first == Cue(0.0, 1.48, "***")
    assert second == Cue(1.6, 4.84, "Der MDR und die Mitteldeutsche\nMedienförderung präsentieren:")
    zdf = cues("zdf-ebutt.xml")[0]
    assert zdf == Cue(0.56, 3.2, "Kannst du noch eins nebenan kleben?\n- Ja, ja, klar.")
    assert [c.text for c in cues("kika-ebutt.xml")[:3]] == ["***", "Haha!", "Juhu!"]


def test_srf_ttml_without_prefix() -> None:
    texts = [c.text for c in cues("srf-ttml.xml")]
    assert texts[1] == "Mit Live-Untertiteln von SWISS TXT"
    assert texts[2] == 'Guten Abend und herzlich willkommen\nbei "Meteo".'
    assert cues("srf-ttml.xml")[2].start == pytest.approx(11.769)


def test_orf_seconds_and_escaped_line_breaks() -> None:
    orf = cues("orf-ttml.ttml")
    assert orf[0] == Cue(0.2, 1.1, "*")
    assert orf[1] == Cue(1.2, 4.0, "Bei den Nobelpreisen\nder Naturwissenschaften")


def test_webvtt_tags_are_removed() -> None:
    srf = cues("srf.vtt")
    assert srf[0] == Cue(0.08, 2.08, ".")
    assert srf[1].text == "Mit Untertiteln von SWISS TXT"
    assert srf[3].text == "Monumentale Gebilde,\nstumme Zeugen der Geschichte."


def test_sr_time_code_is_removed() -> None:
    raw = cues("sr-ebutt-timecode.xml")
    assert raw[1].start == 36001.72  # 10:00:01.720
    for duration in (300, None):  # the film is 5 minutes long; without a length the 10 h rule applies
        shifted = remove_time_code_offset(raw, duration)
        assert shifted[1] == Cue(pytest.approx(1.72), pytest.approx(5.8), "Untertitel:\nARD Text im Auftrag des SR")  # type: ignore[arg-type]
    # A two-hour film whose subtitles really start after one hour keeps its times.
    late = [Cue(3700.0, 3702.0, "spät")]
    assert remove_time_code_offset(late, 7200) == late


def test_srt_output() -> None:
    srt = convert((SUBTITLES / "sr-ebutt-timecode.xml").read_bytes(), duration=300)
    assert srt is not None
    assert srt.startswith("1\n00:00:00,000 --> 00:00:01,040\n.\n\n2\n00:00:01,720 --> 00:00:05,800\nUntertitel:\nARD")
    assert srt.count(" --> ") == 12


def test_unknown_or_broken_input_gives_none() -> None:
    assert convert((SUBTITLES / "ndr-not-subtitles.html").read_bytes()) is None
    assert convert(b"<?xml version='1.0'?><tt xmlns='http://www.w3.org/ns/ttml'><body><p begin=") is None
    assert convert(b"") is None


@pytest.mark.parametrize(
    ("value", "seconds"),
    [("00:01:02.500", 62.5), ("00:00:01:12", 1.5), ("1.5s", 1.5), ("1500ms", 1.5), ("36f", 1.2), ("2m", 120.0)],
)
def test_ttml_time_formats(value: str, seconds: float) -> None:
    data = (
        '<tt xmlns="http://www.w3.org/ns/ttml" xmlns:ttp="http://www.w3.org/ns/ttml#parameter" ttp:frameRate="24">'
        f'<body><div><p begin="{value}" dur="1s">x</p></div></body></tt>'
    ).encode()
    assert parse_ttml(data)[0].start == pytest.approx(seconds if not value.endswith("f") else 36 / 24)
