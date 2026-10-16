"""Shared HTTP client settings: every outgoing request identifies mtdl-web."""

import ipaddress
from collections.abc import Iterable

import httpcore
import httpx

from mtdl_web import USER_AGENT

TIMEOUT = httpx.Timeout(60.0, connect=10.0)


class NotPublic(httpx.TransportError):
    """A url from other people's data (film list, playlist, list of mirrors) leads into the own network."""


def public(address: str) -> bool:
    """An address on the internet: not private, loopback, link-local, shared (100.64/10) or reserved."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])  # an IPv6 zone ("fe80::1%eth0") says nothing here
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


class PublicOnly(httpcore.AsyncNetworkBackend):
    """Connects only to public addresses. It checks the connection itself, not a name lookup before it: a name
    server that answers differently the second time (DNS rebinding) gains nothing. A connection to an address in the
    own network closes again before a single byte of the request is sent."""

    def __init__(self, inner: httpcore.AsyncNetworkBackend) -> None:
        self._inner = inner

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        if not public(host):
            try:
                ipaddress.ip_address(host)
            except ValueError:
                pass  # a name: decided by the address it leads to
            else:
                raise NotPublic(f"{host} is not a public address")
        stream = await self._inner.connect_tcp(host, port, timeout, local_address, socket_options)
        peer = stream.get_extra_info("server_addr")
        if not peer or not public(str(peer[0])):
            await stream.aclose()
            raise NotPublic(f"{host} leads to {peer[0] if peer else 'an unknown address'}, not a public address")
        return stream

    async def connect_unix_socket(
        self, path: str, timeout: float | None = None, socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None
    ) -> httpcore.AsyncNetworkStream:
        raise NotPublic("no unix sockets")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def _public_transport() -> httpx.AsyncHTTPTransport:
    transport = httpx.AsyncHTTPTransport()
    # httpx takes no network backend of its own; its connection pool does (httpcore). Should httpx ever build it
    # differently, mtdl-web refuses to start rather than run without the check (test: test_http.py).
    pool = transport._pool
    if not isinstance(pool, httpcore.AsyncConnectionPool) or not hasattr(pool, "_network_backend"):
        raise RuntimeError("httpx changed: cannot restrict media downloads to public addresses")
    pool._network_backend = PublicOnly(pool._network_backend)
    return transport


def make_client(transport: httpx.AsyncBaseTransport | None = None, *, public_only: bool = False) -> httpx.AsyncClient:
    """AsyncClient with our User-Agent and timeouts; tests pass a mock ``transport``. ``public_only``: for urls that
    come from other people's data, which must not lead into the own network (media, playlists, film list mirrors).
    Plex and webhooks often live there and use the plain client."""
    if public_only and transport is None:
        transport = _public_transport()
    return httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True, transport=transport
    )
