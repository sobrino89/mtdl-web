"""Film list entries as the store writes them, with their hashes. A parallel import computes most of this in
worker processes; only what depends on channel and topic ("same as the previous entry") is left for the end."""

import hashlib
from typing import NamedTuple

from mtdl_web.models import film_id

# Converted fields that do not depend on channel and topic, in film order: description, timestamp, duration, size,
# url, url_low, url_hd, url_subtitle, url_website, geo ("DE-AT-CH", "" = no restriction).
Fields = tuple[str, int | None, int | None, int | None, str | None, str | None, str | None, str | None, str | None, str]

URL_INDEX, WEBSITE_INDEX = 4, 8  # positions in Fields


class Prepared(NamedTuple):
    # Film columns: id, channel, topic, title, then the Fields.
    values: tuple[object, ...]
    identity: int  # channel, topic, url, website: what a diff replaces (Java client)
    content_hash: int  # Fields only: channel, topic, title and url are part of the film id anyway


def stable_hash(values: tuple[object, ...]) -> int:
    data = "\x1f".join("\x00" if v is None else str(v) for v in values).encode()
    return int.from_bytes(hashlib.blake2b(data, digest_size=8).digest()) >> 1


def prepare(channel: str, topic: str, title: str, raw_url: str, fields: Fields, content_hash: int) -> Prepared:
    return Prepared(
        (film_id(channel, topic, title, raw_url), channel, topic, title, *fields),
        stable_hash((channel, topic, fields[URL_INDEX], fields[WEBSITE_INDEX])),
        content_hash,
    )
