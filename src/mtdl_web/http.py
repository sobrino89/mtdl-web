"""Shared HTTP client settings: every outgoing request identifies mtdl-web."""

import httpx

from mtdl_web import USER_AGENT

TIMEOUT = httpx.Timeout(60.0, connect=10.0)


def make_client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    """AsyncClient with our User-Agent and timeouts; tests pass a mock ``transport``."""
    return httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True, transport=transport
    )
