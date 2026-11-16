"""HLS: playlists, keys and segments are fetched here, through the bandwidth limit; ffmpeg only muxes the local
files (``-c copy``), so it never reaches the network. Real cases (docs/filmliste-format.md, test/fixtures/hls/):

- media playlists with muxed TS segments (SRF, ORF chunklists, WDR, SWR, funk), relative or on another host;
- master playlists: ORF (redirect to another host, video-only variants plus an audio rendition), funk (audio-only
  variants and I-frame streams next to the video variants), ARTE (CMAF: every resolution in H.264 and HEVC, the
  sound as separate renditions in several languages, German marked DEFAULT).

Live playlists (no ``#EXT-X-ENDLIST``) are refused: a download would never end.
"""

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin, urlsplit

import httpx

from mtdl_web.downloads import DownloadError
from mtdl_web.downloads.bandwidth import TokenBucket
from mtdl_web.downloads.transfer import fetch, fetch_bytes, refused
from mtdl_web.http import NotPublic
from mtdl_web.models import VideoCodec

Quality = Literal["hd", "normal", "low"]
# ISO 639-1 as the playlists write it, to the ISO 639-2 code that mp4 stores per track (Plex and Jellyfin show it).
# fmt: off
_LANGUAGES = {
    "ar": "ara", "bg": "bul", "cs": "ces", "da": "dan", "de": "deu", "el": "ell", "en": "eng", "es": "spa",
    "fi": "fin", "fr": "fra", "hr": "hrv", "hu": "hun", "it": "ita", "ja": "jpn", "ko": "kor", "nl": "nld",
    "no": "nor", "pl": "pol", "pt": "por", "ro": "ron", "ru": "rus", "sk": "slk", "sl": "slv", "sr": "srp",
    "sv": "swe", "tr": "tur", "uk": "ukr", "zh": "zho",
}
# fmt: on
_ATTRIBUTE = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')
SEGMENT_CONCURRENCY = 4
# Limits against a broken or manipulated source: real playlists have a few KB, and a three-hour broadcast in
# two-second segments has 5,400 of them.
PLAYLIST_LIMIT = 8 << 20
MAX_SEGMENTS = 50_000


def attributes(text: str) -> dict[str, str]:
    return {k: v.strip('"') for k, v in _ATTRIBUTE.findall(text)}


@dataclass(frozen=True, slots=True)
class Variant:
    uri: str
    bandwidth: int
    resolution: tuple[int, int] | None
    codecs: str
    audio_group: str | None


@dataclass(frozen=True, slots=True)
class AudioRendition:
    group: str
    uri: str | None  # None: the audio is muxed into the variant
    language: str | None
    default: bool


@dataclass(frozen=True, slots=True)
class Key:
    method: str
    uri: str | None
    iv: str | None


@dataclass(frozen=True, slots=True)
class InitSection:
    uri: str
    byterange: tuple[int, int] | None  # length, offset


@dataclass(frozen=True, slots=True)
class Segment:
    uri: str
    duration: float
    byterange: tuple[int, int] | None
    key: Key | None
    init: InitSection | None


@dataclass
class MasterPlaylist:
    variants: list[Variant] = field(default_factory=list)
    audio: list[AudioRendition] = field(default_factory=list)


@dataclass
class MediaPlaylist:
    segments: list[Segment] = field(default_factory=list)
    endlist: bool = False
    target_duration: int = 10
    media_sequence: int = 0
    version: int = 3

    @property
    def duration(self) -> float:
        return sum(s.duration for s in self.segments)


def _byterange(value: str, previous_end: int) -> tuple[int, int]:
    length, _, offset = value.partition("@")
    return int(length), int(offset) if offset else previous_end


def parse(text: str, base_url: str) -> MasterPlaylist | MediaPlaylist:
    lines = [line.strip() for line in text.splitlines()]
    if not lines or not lines[0].startswith("#EXTM3U"):
        raise DownloadError(f"not an HLS playlist: {base_url}", code="format")
    if any(line.startswith("#EXT-X-STREAM-INF") for line in lines):
        master = MasterPlaylist()
        pending: dict[str, str] | None = None
        for line in lines:
            if line.startswith("#EXT-X-MEDIA:"):
                a = attributes(line.partition(":")[2])
                if a.get("TYPE") == "AUDIO":
                    uri = urljoin(base_url, a["URI"]) if a.get("URI") else None
                    master.audio.append(
                        AudioRendition(a.get("GROUP-ID", ""), uri, a.get("LANGUAGE"), a.get("DEFAULT") == "YES")
                    )
            elif line.startswith("#EXT-X-STREAM-INF:"):
                pending = attributes(line.partition(":")[2])
            elif line and not line.startswith("#") and pending is not None:
                width, _, height = pending.get("RESOLUTION", "").partition("x")
                resolution = (int(width), int(height)) if width.isdigit() and height.isdigit() else None
                master.variants.append(
                    Variant(
                        urljoin(base_url, line),
                        int(pending.get("BANDWIDTH", "0") or 0),
                        resolution,
                        pending.get("CODECS", ""),
                        pending.get("AUDIO"),
                    )
                )
                pending = None
        return master

    media = MediaPlaylist()
    duration: float | None = None
    byterange: tuple[int, int] | None = None
    key: Key | None = None
    init: InitSection | None = None
    range_end = 0
    for line in lines:
        if line.startswith("#EXTINF:"):
            duration = float(line.partition(":")[2].split(",", 1)[0] or 0)
        elif line.startswith("#EXT-X-BYTERANGE:"):
            byterange = _byterange(line.partition(":")[2], range_end)
        elif line.startswith("#EXT-X-KEY:"):
            a = attributes(line.partition(":")[2])
            method = a.get("METHOD", "NONE")
            key = (
                None
                if method == "NONE"
                else Key(method, urljoin(base_url, a["URI"]) if "URI" in a else None, a.get("IV"))
            )
        elif line.startswith("#EXT-X-MAP:"):
            a = attributes(line.partition(":")[2])
            init = InitSection(urljoin(base_url, a["URI"]), _byterange(a["BYTERANGE"], 0) if "BYTERANGE" in a else None)
        elif line.startswith("#EXT-X-TARGETDURATION:"):
            media.target_duration = int(float(line.partition(":")[2]))
        elif line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            media.media_sequence = int(line.partition(":")[2])
        elif line.startswith("#EXT-X-VERSION:"):
            media.version = int(line.partition(":")[2])
        elif line == "#EXT-X-ENDLIST":
            media.endlist = True
        elif line and not line.startswith("#") and duration is not None:
            media.segments.append(Segment(urljoin(base_url, line), duration, byterange, key, init))
            if byterange:
                range_end = byterange[1] + byterange[0]
            duration, byterange = None, None
    return media


def geo_notice(final_url: str) -> bool:
    """ORF answers a request from outside the allowed countries with a redirect to a notice video
    (``…/insert_geoprotection_hr.smil/playlist.m3u8``, one variant without resolution or codecs): never the
    broadcast, so a download stops there as geo-blocked."""
    return "geoprotection" in urlsplit(final_url).path.lower()


def codec_of(variant: Variant) -> Literal["h264", "hevc"] | None:
    """From CODECS (``avc1.640028``, ``hev1.1.6.L120.B0``); None when the playlist does not say (ORF)."""
    codecs = variant.codecs.lower()
    if "hvc1" in codecs or "hev1" in codecs:
        return "hevc"
    if "avc1" in codecs or "avc3" in codecs:
        return "h264"
    return None


def language_code(language: str | None) -> str | None:
    """The ISO 639-2 code for an audio track (``de`` → ``deu``); three letters stay as they are (``mul``)."""
    code = (language or "").strip().lower().split("-")[0]
    return code if len(code) == 3 and code.isalpha() else _LANGUAGES.get(code)


def choose(
    master: MasterPlaylist, quality: Quality, codec: VideoCodec = "prefer_h264"
) -> tuple[Variant, AudioRendition | None]:
    """The variant for the wanted quality: hd = highest, low = lowest, normal = the highest up to 720p. The
    resolution comes first; where a resolution comes in H.264 and HEVC, ``codec`` picks (a variant without CODECS
    counts as H.264), then the bandwidth as before. ``h264_only`` never takes HEVC: a master with nothing else fails.
    Audio-only variants (funk) and I-frame streams are not candidates."""
    video = [v for v in master.variants if v.resolution or codec_of(v)]
    if not video:
        raise DownloadError("HLS master playlist without video variants", code="format")
    if codec == "h264_only":
        video = [v for v in video if codec_of(v) != "hevc"]
        if not video:
            raise DownloadError("the stream comes only in HEVC, which the settings exclude", code="codec")

    def height(v: Variant) -> int:
        return v.resolution[1] if v.resolution else 0

    preferred = "hevc" if codec == "prefer_hevc" else "h264"

    def first(v: Variant) -> int:  # 1 for the codec the settings prefer
        return int((codec_of(v) or "h264") == preferred)

    up_to_720 = [height(v) for v in video if v.resolution and height(v) <= 720]
    if quality == "hd" or (quality == "normal" and up_to_720):
        top = max(up_to_720) if quality == "normal" else max(height(v) for v in video)
        chosen = max((v for v in video if height(v) == top), key=lambda v: (first(v), v.bandwidth))
    else:  # low, and normal without anything up to 720p: the lowest
        bottom = min(height(v) for v in video)
        chosen = min((v for v in video if height(v) == bottom), key=lambda v: (-first(v), v.bandwidth))
    group = [a for a in master.audio if a.group == chosen.audio_group and a.uri]
    audio = next((a for a in group if a.default), group[0] if group else None)
    return chosen, audio


async def fetch_playlist(client: httpx.AsyncClient, url: str, bucket: TokenBucket) -> tuple[str, str]:
    """Text and final url (relative uris resolve against it; ORF redirects to another host)."""
    try:
        async with client.stream("GET", url) as response:
            if response.status_code != 200:
                raise DownloadError(
                    f"HTTP {response.status_code} for {url}",
                    retryable=response.status_code >= 500,
                    status=response.status_code,
                )
            data = bytearray()
            async for chunk in response.aiter_bytes():
                await bucket.take(len(chunk))
                data += chunk
                if len(data) > PLAYLIST_LIMIT:
                    raise DownloadError(f"{url}: playlist larger than {PLAYLIST_LIMIT} bytes", code="format")
            return data.decode("utf-8", errors="replace"), str(response.url)
    except NotPublic as exc:
        raise refused(exc) from exc


def _extension(uri: str, default: str) -> str:
    suffix = Path(urlsplit(uri).path).suffix.lower()
    return suffix if suffix in (".ts", ".aac", ".mp4", ".m4s", ".m4a", ".m4v", ".mp3", ".vtt") else default


Progress = Callable[[int, int, int], None]  # segments done, segments total, bytes so far


async def download_media(
    client: httpx.AsyncClient,
    playlist: MediaPlaylist,
    directory: Path,
    bucket: TokenBucket,
    progress: Progress,
    *,
    concurrency: int = SEGMENT_CONCURRENCY,
) -> Path:
    """Fetch segments, init sections and keys into ``directory`` (resuming what is there) and write a local
    playlist for ffmpeg; returns its path."""
    if not playlist.endlist:
        raise DownloadError("live stream (HLS playlist without end)", code="livestream")
    if not playlist.segments:
        raise DownloadError("HLS playlist without segments", code="format")
    if any(s.key and s.key.method != "AES-128" for s in playlist.segments):
        raise DownloadError("HLS encryption other than AES-128 is not supported", code="format")
    if len(playlist.segments) > MAX_SEGMENTS:
        raise DownloadError(f"HLS playlist with {len(playlist.segments)} segments", code="format")
    directory.mkdir(parents=True, exist_ok=True)
    total = len(playlist.segments)
    done = sum(
        1 for i, s in enumerate(playlist.segments) if (directory / f"{i:05d}{_extension(s.uri, '.ts')}").exists()
    )
    received = 0

    async def get(uri: str, byterange: tuple[int, int] | None, target: Path) -> None:
        nonlocal received
        if target.exists():
            return
        part = target.with_name(target.name + ".part")
        if byterange:
            # Small by nature; fetched whole, since a resumed range would need a second range.
            length, offset = byterange
            data = await fetch_bytes(client, uri, bucket, headers={"Range": f"bytes={offset}-{offset + length - 1}"})
            if len(data) != length:
                raise DownloadError(
                    f"{uri}: expected {length} bytes of the range, got {len(data)}", retryable=True, code="network"
                )
            part.write_bytes(data)
            received += length
        else:
            size_before = part.stat().st_size if part.exists() else 0
            size = await fetch(client, uri, part, bucket)
            received += size - size_before
        part.replace(target)

    # Keys and init sections first: few and small, and the playlist needs their local names.
    local_keys: dict[str, str] = {}
    local_inits: dict[InitSection, str] = {}
    for segment in playlist.segments:
        if segment.key and segment.key.uri and segment.key.uri not in local_keys:
            name = f"key{len(local_keys)}.bin"
            await get(segment.key.uri, None, directory / name)
            local_keys[segment.key.uri] = name
        if segment.init and segment.init not in local_inits:
            name = f"init{len(local_inits)}{_extension(segment.init.uri, '.mp4')}"
            await get(segment.init.uri, segment.init.byterange, directory / name)
            local_inits[segment.init] = name

    semaphore = asyncio.Semaphore(concurrency)

    async def segment_task(index: int, segment: Segment) -> None:
        nonlocal done
        target = directory / f"{index:05d}{_extension(segment.uri, '.ts')}"
        if target.exists():
            return
        async with semaphore:
            await get(segment.uri, segment.byterange, target)
        done += 1
        progress(done, total, received)

    progress(done, total, received)
    tasks = [asyncio.create_task(segment_task(i, s)) for i, s in enumerate(playlist.segments)]
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        # gather passes the first error on but leaves the other segments loading: stop them before the download
        # counts as failed, or they keep using the bandwidth and write into parts that a retry appends to.
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise

    lines = [
        "#EXTM3U",
        f"#EXT-X-VERSION:{playlist.version}",
        f"#EXT-X-TARGETDURATION:{playlist.target_duration}",
        f"#EXT-X-MEDIA-SEQUENCE:{playlist.media_sequence}",  # implicit AES IVs are the sequence numbers
        "#EXT-X-PLAYLIST-TYPE:VOD",
    ]
    current_key: Key | None = None
    current_init: InitSection | None = None
    for index, segment in enumerate(playlist.segments):
        if segment.key != current_key:
            if segment.key is None:
                lines.append("#EXT-X-KEY:METHOD=NONE")
            else:
                iv = f",IV={segment.key.iv}" if segment.key.iv else ""
                lines.append(f'#EXT-X-KEY:METHOD={segment.key.method},URI="{local_keys[segment.key.uri or ""]}"{iv}')
            current_key = segment.key
        if segment.init != current_init and segment.init is not None:
            lines.append(f'#EXT-X-MAP:URI="{local_inits[segment.init]}"')
            current_init = segment.init
        lines.append(f"#EXTINF:{segment.duration:.3f},")
        lines.append(f"{index:05d}{_extension(segment.uri, '.ts')}")
    lines.append("#EXT-X-ENDLIST")
    local = directory / "local.m3u8"
    local.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return local


def mux_args(
    video: Path,
    audio: Path | None,
    output: Path,
    *,
    ffmpeg: str = "ffmpeg",
    hevc: bool = False,
    audio_language: str | None = None,
) -> list[str]:
    """ffmpeg's command line for ``mux``. HEVC is written as ``hvc1``, the tag Apple's players need (ARTE sends
    ``hev1``; mpv, ExoPlayer and ffmpeg take both). ``audio_language`` labels the separate audio rendition."""
    hls_input = ["-allowed_extensions", "ALL", "-protocol_whitelist", "file,crypto"]
    args = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *hls_input, "-i", str(video)]
    if audio is not None:
        args += [*hls_input, "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0"]
        if code := language_code(audio_language):
            args += ["-metadata:s:a:0", f"language={code}"]
    else:
        args += ["-map", "0:v?", "-map", "0:a?"]
    args += ["-c", "copy"]
    if hevc:
        args += ["-tag:v", "hvc1"]
    return [*args, "-movflags", "+faststart", "-f", "mp4", str(output)]


async def mux(
    video: Path,
    audio: Path | None,
    output: Path,
    *,
    ffmpeg: str = "ffmpeg",
    hevc: bool = False,
    audio_language: str | None = None,
) -> None:
    """Local HLS playlist(s) to mp4 without re-encoding; only file and crypto protocols are allowed."""
    args = mux_args(video, audio, output, ffmpeg=ffmpeg, hevc=hevc, audio_language=audio_language)
    process = await asyncio.create_subprocess_exec(
        *args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        raise DownloadError(
            f"ffmpeg failed ({process.returncode}): {stderr.decode(errors='replace')[-600:]}", code="mux"
        )
