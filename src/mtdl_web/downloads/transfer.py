"""HTTP transfer of one file with resume: bytes go to ``<target>.part`` and through the bandwidth limit; an
interrupted transfer continues with a Range request, also after a restart."""

import asyncio
from collections.abc import Callable
from pathlib import Path

import httpx

from mtdl_web.downloads import DownloadError
from mtdl_web.downloads.bandwidth import TokenBucket

RETRIES = 4

Progress = Callable[[int, int | None], None]  # bytes so far, total if known


def _no_progress(done: int, total: int | None) -> None:
    pass


def _gave_up(exc: Exception, url: str) -> DownloadError:
    """The error after the last retry: keeps the server's status, else it was the network."""
    if isinstance(exc, DownloadError):
        return DownloadError(str(exc), retryable=True, status=exc.status, code=exc.code)
    return DownloadError(f"{url}: {exc}", retryable=True, code="network")


def _content_range_total(value: str | None) -> int | None:
    # "bytes 100-199/5000" or "bytes */5000"
    if not value or "/" not in value:
        return None
    total = value.rsplit("/", 1)[1].strip()
    return int(total) if total.isdigit() else None


async def fetch(
    client: httpx.AsyncClient,
    url: str,
    part: Path,
    bucket: TokenBucket,
    progress: Progress = _no_progress,
    *,
    headers: dict[str, str] | None = None,
    retries: int = RETRIES,
    retry_delay: float = 2.0,
) -> int:
    """Download ``url`` into ``part`` (appending to what is there) and return its size. The caller renames it."""
    attempt = 0
    while True:
        offset = part.stat().st_size if part.exists() else 0
        request_headers = dict(headers or {})
        if offset:
            request_headers["Range"] = f"bytes={offset}-"
        try:
            async with client.stream("GET", url, headers=request_headers) as response:
                status = response.status_code
                if status == 416 and offset:
                    # Nothing after the offset: complete if the server says the file has exactly this size.
                    if _content_range_total(response.headers.get("content-range")) == offset:
                        return offset
                    part.unlink()
                    continue
                if status == 206 and offset:
                    total = _content_range_total(response.headers.get("content-range"))
                elif status == 200:
                    offset = 0  # no range support (or nothing to resume): start over
                    length = response.headers.get("content-length")
                    total = int(length) if length and length.isdigit() else None
                else:
                    retryable = status in (408, 425, 429) or status >= 500
                    raise DownloadError(f"HTTP {status} for {url}", retryable=retryable, status=status)
                with part.open("ab" if offset else "wb") as out:
                    # Chunks as they arrive: a broken connection loses nothing that was received.
                    async for chunk in response.aiter_bytes():
                        await bucket.take(len(chunk))
                        out.write(chunk)
                        offset += len(chunk)
                        progress(offset, total)
                if total is not None and offset < total:
                    raise httpx.ReadError(f"connection closed after {offset} of {total} bytes")
                return offset
        except (httpx.TransportError, DownloadError) as exc:
            if isinstance(exc, DownloadError) and not exc.retryable:
                raise
            attempt += 1
            if attempt > retries:
                raise _gave_up(exc, url) from exc
            await asyncio.sleep(retry_delay * 2 ** (attempt - 1))


async def fetch_bytes(
    client: httpx.AsyncClient,
    url: str,
    bucket: TokenBucket,
    *,
    headers: dict[str, str] | None = None,
    limit: int = 32 << 20,
    retries: int = RETRIES,
    retry_delay: float = 2.0,
) -> bytes:
    """Small resources (keys, subtitles, byte-range segments), also through the limit."""
    attempt = 0
    while True:
        try:
            async with client.stream("GET", url, headers=headers) as response:
                if response.status_code not in (200, 206):
                    retryable = response.status_code in (408, 425, 429) or response.status_code >= 500
                    raise DownloadError(
                        f"HTTP {response.status_code} for {url}", retryable=retryable, status=response.status_code
                    )
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    await bucket.take(len(chunk))
                    data += chunk
                    if len(data) > limit:
                        raise DownloadError(f"{url}: larger than {limit} bytes", code="format")
                return bytes(data)
        except (httpx.TransportError, DownloadError) as exc:
            if isinstance(exc, DownloadError) and not exc.retryable:
                raise
            attempt += 1
            if attempt > retries:
                raise _gave_up(exc, url) from exc
            await asyncio.sleep(retry_delay * 2 ** (attempt - 1))
