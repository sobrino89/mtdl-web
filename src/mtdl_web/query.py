"""Search syntax of MediathekViewWeb, so its users find their way: selectors narrow words to one field.

``!ard`` channel, ``#tatort`` topic, ``+mord`` title, ``*berlin`` description, ``>80`` at least 80 minutes,
``<10`` at most 10 minutes. Words after a selector joined by commas must all occur (``#sturm,der,liebe``); the same
selector twice means either (``!ard !zdf``); everything else must match together. Free words search topic and title,
only the title once a topic selector is given (as MediathekViewWeb does). Case does not matter. Every word counts
as a word prefix, like the free words. Both sources understand the syntax, so subscriptions may use it too.
"""

import math
from dataclasses import dataclass, fields

SELECTORS = {"!": "channels", "#": "topics", "+": "titles", "*": "descriptions"}
MAX_MINUTES = 100_000  # longer than any broadcast; keeps the bound inside SQLite integers


@dataclass(frozen=True, slots=True)
class Query:
    words: tuple[str, ...] = ()
    # One entry per selector: words that must all occur; the entries of one field are alternatives.
    channels: tuple[tuple[str, ...], ...] = ()
    topics: tuple[tuple[str, ...], ...] = ()
    titles: tuple[tuple[str, ...], ...] = ()
    descriptions: tuple[tuple[str, ...], ...] = ()
    min_seconds: int | None = None
    max_seconds: int | None = None

    @property
    def empty(self) -> bool:
        return not any(getattr(self, f.name) not in ((), None) for f in fields(self))


def _minutes(text: str) -> int | None:
    try:
        value = float(text.replace(",", "."))
    except ValueError:
        return None
    return round(min(value, MAX_MINUTES) * 60) if math.isfinite(value) and value >= 0 else None


def parse(text: str) -> Query:
    words: list[str] = []
    groups: dict[str, list[tuple[str, ...]]] = {name: [] for name in SELECTORS.values()}
    bounds: dict[str, int | None] = {">": None, "<": None}
    for token in text.lower().split():
        if field := SELECTORS.get(token[0]):
            if parts := tuple(p for p in token[1:].split(",") if p):
                groups[field].append(parts)
        elif token[0] in bounds:
            if (seconds := _minutes(token[1:])) is not None:
                bounds[token[0]] = seconds
        else:
            words.append(token)
    return Query(
        words=tuple(words),
        channels=tuple(groups["channels"]),
        topics=tuple(groups["topics"]),
        titles=tuple(groups["titles"]),
        descriptions=tuple(groups["descriptions"]),
        min_seconds=bounds[">"],
        max_seconds=bounds["<"],
    )
