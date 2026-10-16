"""Requests for urls from other people's data (film list, playlists, mirrors) reach only public addresses: checked
on the connection itself, with a real server on this machine, so that the check is known to be in the client."""

import asyncio
from collections.abc import AsyncIterator, Iterable
from typing import Any

import httpcore
import pytest

from mtdl_web.http import NotPublic, PublicOnly, make_client, public


def test_which_addresses_are_public() -> None:
    assert all(public(a) for a in ("9.9.9.9", "2606:4700::1111", "::ffff:9.9.9.9"))
    inside = ("10.0.0.5", "127.0.0.1", "0.0.0.0", "169.254.169.254", "100.64.0.1", "192.168.0.1", "::1", "fe80::1%eth0")
    assert not any(public(a) for a in (*inside, "fd00::1", "::ffff:10.0.0.1", "224.0.0.1", "example.org", ""))


class Server:
    """A local HTTP server that notes what reaches it."""

    def __init__(self) -> None:
        self.connections = 0
        self.received = b""

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        self.received += await reader.read(1000)
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        await writer.drain()
        writer.close()


@pytest.fixture
async def server() -> AsyncIterator[tuple[Server, int]]:
    seen = Server()
    listening = await asyncio.start_server(seen.handle, "127.0.0.1", 0)
    async with listening:
        yield seen, listening.sockets[0].getsockname()[1]


@pytest.mark.anyio
async def test_the_media_client_does_not_reach_the_own_network(server: tuple[Server, int]) -> None:
    seen, port = server
    async with make_client() as plain:  # Plex and webhooks: the own network is fine
        assert (await plain.get(f"http://127.0.0.1:{port}/")).text == "ok"
    seen.connections, seen.received = 0, b""
    async with make_client(public_only=True) as media:
        for url in (f"http://127.0.0.1:{port}/x", f"http://localhost:{port}/x", "http://10.0.0.5/x", "http://[::1]/"):
            with pytest.raises(NotPublic):
                await media.get(url)
    # Addresses in the url: refused before connecting. The name: connected, but not a byte of the request went out.
    assert (seen.connections, seen.received) == (1, b"")


class FakeStream:
    def __init__(self, peer: tuple[str, int] | None) -> None:
        self.peer = peer
        self.closed = False

    def get_extra_info(self, info: str) -> Any:
        return self.peer if info == "server_addr" else None

    async def aclose(self) -> None:
        self.closed = True


class FakeBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, peer: tuple[str, int] | None) -> None:
        self.stream = FakeStream(peer)
        self.asked: list[str] = []

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self.asked.append(host)
        return self.stream  # type: ignore[return-value]


@pytest.mark.anyio
async def test_the_address_connected_to_decides() -> None:
    """A name that pointed outside at the check and inside at the connection (DNS rebinding) is no way in."""
    cdn = FakeBackend(("9.9.9.9", 443))
    assert await PublicOnly(cdn).connect_tcp("cdn.example", 443) is cdn.stream  # type: ignore[comparison-overlap]
    rebound = FakeBackend(("192.168.0.1", 443))
    with pytest.raises(NotPublic) as refused:
        await PublicOnly(rebound).connect_tcp("cdn.example", 443)
    assert str(refused.value) == "cdn.example leads to 192.168.0.1, not a public address"
    assert rebound.stream.closed
    literal = FakeBackend(("10.0.0.5", 80))
    with pytest.raises(NotPublic):
        await PublicOnly(literal).connect_tcp("10.0.0.5", 80)
    assert literal.asked == []  # an address in the url: refused without connecting
