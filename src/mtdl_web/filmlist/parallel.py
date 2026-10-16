"""Parallel film list import (``MTDL_WORKERS``).

The main process decompresses the list and cuts it into pieces of whole entries; worker processes parse and convert
them and compute the content hash. Back in order, the main process fills in channel and topic ("same as the previous
entry", which crosses piece boundaries), derives the two hashes that depend on them and writes to SQLite, the one part
that cannot run in parallel. Measured on the real list (October 2026): first import 77 s sequential, 58 s parallel; a
full re-import 43 s and 28 s, the same with 2, 3 or 12 workers, since the writer is the bottleneck.

Workers start through a fork server (never fork a process that runs threads). Like every spawned process they
import the main module again: an entry point script needs the ``if __name__ == "__main__":`` guard.
"""

import collections
import concurrent.futures
import contextlib
import json
import multiprocessing
import re
from collections.abc import Iterator
from pathlib import Path
from typing import TextIO

from mtdl_web.filmlist.parser import (
    CHANNEL,
    MAX_VALUE,
    TITLE,
    TOPIC,
    URL,
    FilmListError,
    FilmListReader,
    check_header,
    check_row,
    convert_fields,
    open_text,
)
from mtdl_web.filmlist.prepared import Fields, Prepared, prepare, stable_hash
from mtdl_web.filmlist.store import ImportSource

PIECE_CHARS = 1 << 20
# The writer is the bottleneck: one parser process keeps up (re-import of the full list 28.5 s with one, 28.1 s with
# eleven, 13.10.2026). The second one is headroom for slower cores; more only cost memory.
MAX_PARSE_PROCESSES = 2
HEADER_CHARS = 1 << 16  # the two header values take about 500 characters
# Between two entries. It cannot occur inside a JSON string: a quote there is always escaped ("\"").
_SEPARATOR = re.compile(r'\s*,\s*"X"\s*:\s*')
_HEADER = re.compile(r'\s*\{\s*"Filmliste"\s*:\s*')
_SECOND_HEADER = re.compile(r'\s*,\s*"Filmliste"\s*:\s*')
_decoder = json.JSONDecoder()

# Raw channel, raw topic (both may be empty), title, raw main url, converted fields, content hash.
Partial = tuple[str, str, str, str, Fields, int]


def parse_piece(text: str) -> list[Partial]:
    """Worker side: entries separated by ',"X":'."""
    out: list[Partial] = []
    pos, end = 0, len(text)
    while True:
        while pos < end and text[pos].isspace():
            pos += 1
        try:
            raw, pos = _decoder.raw_decode(text, pos)
        except json.JSONDecodeError as exc:
            raise FilmListError(f"invalid JSON: {exc.msg}") from exc
        row = check_row(raw)
        fields = convert_fields(row)
        out.append((row[CHANNEL], row[TOPIC], row[TITLE], row[URL], fields, stable_hash(fields)))
        separator = _SEPARATOR.match(text, pos)
        if separator is None:
            if text[pos:].strip():
                raise FilmListError("expected another entry")
            return out
        pos = separator.end()


class ParallelFilmListReader:
    """Same import interface as ``FilmListReader`` (``meta``, ``prepared()``)."""

    def __init__(
        self, stream: TextIO, workers: int, piece_chars: int = PIECE_CHARS, max_entry_chars: int = MAX_VALUE
    ) -> None:
        self._stream = stream
        self._processes = max(1, min(workers - 1, MAX_PARSE_PROCESSES))  # the main process writes
        self._piece_chars = piece_chars
        self._max_entry_chars = max_entry_chars
        buf = stream.read(max(piece_chars, HEADER_CHARS))
        try:
            head = _HEADER.match(buf)
            if head is None:
                raise FilmListError("missing film list header")
            meta, pos = _decoder.raw_decode(buf, head.end())
            second = _SECOND_HEADER.match(buf, pos)
            if second is None:
                raise FilmListError("missing film list header")
            columns, pos = _decoder.raw_decode(buf, second.end())
        except json.JSONDecodeError as exc:
            raise FilmListError(f"invalid JSON: {exc.msg}") from exc
        self.meta, *header = check_header(meta, columns)
        self.header = (header[0], header[1])
        first = _SEPARATOR.match(buf, pos)
        self._buf, self._start = buf, (first.end() if first else pos)
        self._has_entries = first is not None

    def _pieces(self) -> Iterator[str]:
        """Whole entries, at least ``piece_chars`` per piece. As in the sequential reader, an entry beyond
        ``max_entry_chars`` is an error: a manipulated list must not fill the memory. The search for the next
        separator reads every character once; one that crosses the end of the buffer starts at its last comma."""
        buf, start = self._buf, self._start
        if not self._has_entries:
            if buf[start:].strip() != "}":
                raise FilmListError("unexpected end of input")
            return
        resume = 0  # in buf: no separator starts before this position
        while True:
            begin = max(resume, start + self._piece_chars)
            separator = _SEPARATOR.search(buf, begin)
            if separator is not None:
                if separator.start() - (start + self._piece_chars) > self._max_entry_chars:
                    raise FilmListError(f"an entry is longer than {self._max_entry_chars} characters")
                yield buf[start : separator.start()]
                start = separator.end()
                continue
            if len(buf) - start > self._piece_chars + self._max_entry_chars:
                raise FilmListError(f"an entry is longer than {self._max_entry_chars} characters")
            data = self._stream.read(self._piece_chars)
            if data:
                comma = buf.rfind(",", begin)
                resume = (comma if comma != -1 else len(buf)) - start
                buf, start = buf[start:] + data, 0
                continue
            rest = buf[start:].rstrip()
            if not rest.endswith("}"):
                raise FilmListError("unexpected end of input")
            yield rest[:-1]
            return

    def prepared(self) -> Iterator[Prepared]:
        context = multiprocessing.get_context("forkserver")
        pool = concurrent.futures.ProcessPoolExecutor(self._processes, mp_context=context)
        try:
            pieces = self._pieces()
            pending: collections.deque[concurrent.futures.Future[list[Partial]]] = collections.deque()

            def submit() -> None:
                piece = next(pieces, None)
                if piece is not None:
                    pending.append(pool.submit(parse_piece, piece))

            for _ in range(2 * self._processes):  # bounded: the list never sits in memory as a whole
                submit()
            channel = topic = ""
            while pending:
                partials = pending.popleft().result()
                submit()
                for raw_channel, raw_topic, title, raw_url, fields, content_hash in partials:
                    channel = raw_channel or channel
                    topic = raw_topic or topic
                    yield prepare(channel, topic, title, raw_url, fields, content_hash)
        finally:
            pool.shutdown(wait=True, cancel_futures=True)


@contextlib.contextmanager
def open_for_import(path: Path, workers: int) -> Iterator[ImportSource]:
    """A film list for ``FilmStore.import_full``/``import_diff``; parallel from two workers on."""
    with open_text(path) as text:
        yield FilmListReader(text) if workers <= 1 else ParallelFilmListReader(text, workers)
