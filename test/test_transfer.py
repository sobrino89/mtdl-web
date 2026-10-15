import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from conftest import FULL_EXTRACT

from mtdl_web.downloads import DownloadError
from mtdl_web.downloads.bandwidth import TokenBucket
from mtdl_web.downloads.transfer import fetch, fetch_bytes

PAYLOAD = FULL_EXTRACT.read_bytes()  # any real bytes do; 165 KB


class FlakyServer:
    """Serves PAYLOAD with Range support; can break the connection after some bytes or ignore ranges."""

    def __init__(self) -> None:
        self.break_after: list[int] = []  # per request: drop the connection after this many bytes
        self.ignore_ranges = False
        self.status: int | None = None
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status:
            return httpx.Response(self.status)
        start = 0
        header = request.headers.get("range")
        if header and not self.ignore_ranges:
            start = int(header.removeprefix("bytes=").rstrip("-"))
            if start >= len(PAYLOAD):
                return httpx.Response(416, headers={"content-range": f"bytes */{len(PAYLOAD)}"})
        body = PAYLOAD[start:]
        limit = self.break_after.pop(0) if self.break_after else None

        async def stream() -> AsyncIterator[bytes]:
            sent = 0
            for i in range(0, len(body), 8192):
                chunk = body[i : i + 8192]
                if limit is not None and sent + len(chunk) > limit:
                    yield chunk[: limit - sent]
                    raise httpx.ReadError("connection reset")
                sent += len(chunk)
                yield chunk

        headers = {"content-length": str(len(body))}
        if start:
            headers["content-range"] = f"bytes {start}-{len(PAYLOAD) - 1}/{len(PAYLOAD)}"
        return httpx.Response(206 if start else 200, headers=headers, content=stream())


@pytest.fixture
def server() -> FlakyServer:
    return FlakyServer()


async def download(server: FlakyServer, part: Path, **kwargs: object) -> int:
    async with httpx.AsyncClient(transport=httpx.MockTransport(server.handler)) as client:
        return await fetch(client, "https://cdn.example/film.mp4", part, TokenBucket(), retry_delay=0, **kwargs)  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_plain_download(tmp_path: Path, server: FlakyServer) -> None:
    progress: list[tuple[int, int | None]] = []
    part = tmp_path / "film.mp4.part"
    async with httpx.AsyncClient(transport=httpx.MockTransport(server.handler)) as client:
        size = await fetch(
            client, "https://cdn.example/film.mp4", part, TokenBucket(), lambda d, t: progress.append((d, t))
        )
    assert size == len(PAYLOAD)
    assert part.read_bytes() == PAYLOAD
    assert progress[-1] == (len(PAYLOAD), len(PAYLOAD))


@pytest.mark.anyio
async def test_resumes_after_broken_connections(tmp_path: Path, server: FlakyServer) -> None:
    server.break_after = [50_000, 30_000]
    part = tmp_path / "film.mp4.part"
    assert await download(server, part) == len(PAYLOAD)
    assert part.read_bytes() == PAYLOAD
    assert [r.headers.get("range") for r in server.requests] == [None, "bytes=50000-", "bytes=80000-"]


@pytest.mark.anyio
async def test_resumes_a_part_file_from_an_earlier_run(tmp_path: Path, server: FlakyServer) -> None:
    part = tmp_path / "film.mp4.part"
    part.write_bytes(PAYLOAD[:12_345])
    assert await download(server, part) == len(PAYLOAD)
    assert part.read_bytes() == PAYLOAD
    assert server.requests[0].headers["range"] == "bytes=12345-"


@pytest.mark.anyio
async def test_complete_part_file_and_server_without_ranges(tmp_path: Path, server: FlakyServer) -> None:
    part = tmp_path / "film.mp4.part"
    part.write_bytes(PAYLOAD)
    assert await download(server, part) == len(PAYLOAD)  # 416 with the full size: done
    part.write_bytes(PAYLOAD[:1000])
    server.ignore_ranges = True  # 200 instead of 206: start over instead of appending
    assert await download(server, part) == len(PAYLOAD)
    assert part.read_bytes() == PAYLOAD


@pytest.mark.anyio
async def test_errors(tmp_path: Path, server: FlakyServer) -> None:
    server.status = 404
    with pytest.raises(DownloadError) as caught:
        await download(server, tmp_path / "a.part")
    assert not caught.value.retryable
    assert len(server.requests) == 1  # no retries for a missing file
    server.status = 503
    with pytest.raises(DownloadError) as caught:
        await download(server, tmp_path / "b.part", retries=2)
    assert caught.value.retryable
    assert len(server.requests) == 1 + 3
    server.status = None
    server.break_after = [1000] * 10
    with pytest.raises(DownloadError):
        await download(server, tmp_path / "c.part", retries=2)


@pytest.mark.anyio
async def test_fetch_bytes(server: FlakyServer) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(server.handler)) as client:
        assert await fetch_bytes(client, "https://cdn.example/x", TokenBucket()) == PAYLOAD
        with pytest.raises(DownloadError):
            await fetch_bytes(client, "https://cdn.example/x", TokenBucket(), limit=1000)


@pytest.mark.anyio
async def test_bandwidth_limit_holds_the_rate() -> None:
    bucket = TokenBucket(rate=2_000_000)  # 2 MB/s, burst 0.5 s
    started = time.monotonic()
    for _ in range(40):
        await bucket.take(50_000)  # 2 MB in total
    elapsed = time.monotonic() - started
    assert 0.9 <= elapsed <= 1.4  # (2 MB - nothing saved up) / 2 MB/s, minus scheduling slack


@pytest.mark.anyio
async def test_bandwidth_limit_is_shared_and_changes_at_once() -> None:
    bucket = TokenBucket(rate=1_000_000)

    async def worker() -> None:
        for _ in range(10):
            await bucket.take(50_000)

    started = time.monotonic()
    await asyncio.gather(worker(), worker())  # 1 MB together at 1 MB/s
    assert 0.8 <= time.monotonic() - started <= 1.3
    bucket.set_rate(10_000)  # 10 KB/s: a 1 MB transfer would take 100 s ...
    task = asyncio.create_task(worker())
    await asyncio.sleep(0.2)
    started = time.monotonic()
    bucket.set_rate(0)  # ... until the limit is lifted
    await task
    assert time.monotonic() - started < 0.2
