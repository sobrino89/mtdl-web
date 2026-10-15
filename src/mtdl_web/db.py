"""SQLite helpers: connections, transactions, migrations."""

import contextlib
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

if sqlite3.threadsafety != 3:  # pragma: no cover - the image pins SQLite
    raise RuntimeError("SQLite must be built in serialized mode: connections are shared between threads")


def connect(path: Path | str) -> sqlite3.Connection:
    """Autocommit connection; group writes with ``transaction()``. Use it from one thread at a time, or wrap it
    in ``Database``."""
    con = sqlite3.connect(path, autocommit=True, check_same_thread=False)
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA synchronous = NORMAL")
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 10000")
    return con


@contextlib.contextmanager
def transaction(con: sqlite3.Connection) -> Iterator[None]:
    con.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        con.execute("ROLLBACK")
        raise
    con.execute("COMMIT")


def migrate(con: sqlite3.Connection, migrations: Sequence[str]) -> None:
    """Apply every migration not yet applied; ``PRAGMA user_version`` counts the applied ones."""
    (version,) = con.execute("PRAGMA user_version").fetchone()
    for number in range(version, len(migrations)):
        try:
            con.executescript(f"BEGIN IMMEDIATE;\n{migrations[number]}\nPRAGMA user_version = {number + 1};\nCOMMIT;")
        except BaseException:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise


class Database:
    """A connection used by several threads (event loop, thread pool, import thread).

    The sqlite3 module caches prepared statements per connection; two threads running the same SQL at once corrupt
    each other's results (seen 13.10.2026: ``SELECT count(*)`` returned no row while the API polled during an
    import). So every call runs under one lock and returns its rows; no cursor outlives the lock.
    """

    def __init__(self, path: Path | str, *, query_only: bool = False) -> None:
        self._con = connect(path)
        if query_only:
            self._con.execute("PRAGMA query_only = 1")
        self._lock = threading.RLock()

    def close(self) -> None:
        with self._lock:
            self._con.close()

    def one(self, sql: str, params: Sequence[object] = ()) -> tuple[Any, ...] | None:
        with self._lock:
            row: tuple[Any, ...] | None = self._con.execute(sql, params).fetchone()
            return row

    def all(self, sql: str, params: Sequence[object] = ()) -> list[tuple[Any, ...]]:
        with self._lock:
            return self._con.execute(sql, params).fetchall()

    def run(self, sql: str, params: Sequence[object] = ()) -> tuple[int, int | None]:
        """A writing statement: (rows changed, last row id)."""
        with self._lock:
            cursor = self._con.execute(sql, params)
            return cursor.rowcount, cursor.lastrowid

    @contextlib.contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Several statements as one transaction; the lock is held throughout."""
        with self._lock, transaction(self._con):
            yield self._con

    def migrate(self, migrations: Sequence[str]) -> None:
        with self._lock:
            migrate(self._con, migrations)
