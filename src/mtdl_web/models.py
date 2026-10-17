"""Data model shared by all sources."""

import datetime as dt
import hashlib
from dataclasses import dataclass
from typing import Literal

# Where a search looks: topic and title (MediathekViewWeb default), only one of them, or also descriptions.
Scope = Literal["topic_title", "title", "topic", "all"]


def film_id(channel: str, topic: str, title: str, url: str) -> int:
    """Stable 63-bit id of a film list entry.

    (channel, topic, title, main url) is unique in the real film list (checked on 719,925 entries, 12.10.2026).
    Never change this function: ids are stored.
    """
    data = "\x1f".join((channel, topic, title, url)).encode()
    return int.from_bytes(hashlib.blake2b(data, digest_size=8).digest()) >> 1


@dataclass(frozen=True, slots=True)
class Film:
    id: int
    channel: str
    topic: str
    title: str
    description: str
    timestamp: int | None  # broadcast start in Unix seconds, None if the list has no date
    duration: int | None  # seconds
    size: int | None  # bytes of the normal-quality file
    url: str | None  # normal quality, None if the list entry is broken
    url_low: str | None
    url_hd: str | None
    url_subtitle: str | None
    url_website: str | None
    geo: tuple[str, ...]  # countries the stream is restricted to, empty = no restriction

    @property
    def start(self) -> dt.datetime | None:
        return None if self.timestamp is None else dt.datetime.fromtimestamp(self.timestamp, dt.UTC)


@dataclass(frozen=True, slots=True)
class SearchResult:
    films: list[Film]
    total: int  # all hits, not only this page
