import logging

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
