import logging
import os

import pytest

from mtdl_web import healthcheck
from mtdl_web.__main__ import QuietPolling


def access(path: str, status: int) -> logging.LogRecord:
    """An access log record as uvicorn writes it."""
    args = ("192.0.2.1:50000", "GET", path, "1.1", status)
    return logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d', args, None)


def test_polling_leaves_no_access_lines() -> None:
    quiet = QuietPolling()
    assert not quiet.filter(access("/api/v1/status", 200))
    assert not quiet.filter(access("/api/health", 200))
    assert quiet.filter(access("/api/health", 503))  # a degraded health check is worth a line
    assert quiet.filter(access("/api/v1/search?q=tatort", 200))
    assert quiet.filter(logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, "plain message", None, None))


def test_the_health_check_leaves_root_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """Docker starts the health check as the image's user, root; it becomes PUID:PGID like the app."""
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(os, "getuid", lambda: 0)
    monkeypatch.setattr(os, "setgroups", lambda groups: calls.append(("groups", groups)))
    monkeypatch.setattr(os, "setgid", lambda gid: calls.append(("gid", gid)))
    monkeypatch.setattr(os, "setuid", lambda uid: calls.append(("uid", uid)))
    monkeypatch.setenv("PUID", "1000")
    monkeypatch.setenv("PGID", "1001")
    healthcheck.drop_root()
    assert calls == [("groups", []), ("gid", 1001), ("uid", 1000)]  # the user last: afterwards it could not
    calls.clear()
    monkeypatch.delenv("PUID")
    monkeypatch.setenv("PGID", "")
    healthcheck.drop_root()
    assert calls == [("groups", []), ("gid", 100), ("uid", 99)]  # the defaults of docker/entrypoint.sh
    calls.clear()
    monkeypatch.setattr(os, "getuid", lambda: 1000)  # user: in Compose
    healthcheck.drop_root()
    assert calls == []
