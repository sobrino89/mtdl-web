"""Identity of a broadcast: never the url alone (hosts change within a series), but topic, title, day and
duration.

Measured on the full list of 12.10.2026 (722,672 entries): the key topic + title + day groups 20,947 sets of
entries; 14,703 span several channels (3sat/ZDF, ARD/NDR: the same broadcast). Within one channel, 102 sets differ
by more than ten minutes (full episode and excerpt under one title), hence the duration check on top of the key.

The same video file on the same day is the same broadcast too, whatever channel and title say: 3sat lists ZDF's
"Die Anstalt" under its own title (13.10.2026). In the list of that day 524 pairs share file, day and duration, 49 of
them already share the key. The day keeps "newest episode" addresses apart that stay while the content changes.
"""

import datetime as dt
import zoneinfo

from mtdl_web.models import Film
from mtdl_web.text import fold

BERLIN = zoneinfo.ZoneInfo("Europe/Berlin")


def broadcast_day(film: Film, timezone: dt.tzinfo = BERLIN) -> str:
    return dt.datetime.fromtimestamp(film.timestamp, timezone).date().isoformat() if film.timestamp is not None else ""


def episode_key(film: Film, timezone: dt.tzinfo = BERLIN) -> str:
    return f"{fold(film.topic)}|{fold(film.title)}|{broadcast_day(film, timezone)}"


def videos(film: Film) -> frozenset[str]:
    """The film's video files in every quality."""
    return frozenset(url for url in (film.url, film.url_hd, film.url_low) if url)


def same_file(a: Film, b: Film, timezone: dt.tzinfo = BERLIN) -> bool:
    """One video file on one day and of one length: one broadcast, whatever channel and title say."""
    return (
        bool(videos(a) & videos(b))
        and broadcast_day(a, timezone) == broadcast_day(b, timezone)
        and same_duration(a.duration, b.duration)
    )


def same_duration(a: int | None, b: int | None) -> bool:
    """Within one minute or ten percent; unknown durations do not tell the films apart."""
    if a is None or b is None:
        return True
    return abs(a - b) <= max(60, 0.1 * max(a, b))
