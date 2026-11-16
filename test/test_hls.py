import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from mtdl_web.downloads import DownloadError
from mtdl_web.downloads.bandwidth import TokenBucket
from mtdl_web.downloads.hls import (
    MAX_SEGMENTS,
    PLAYLIST_LIMIT,
    MasterPlaylist,
    MediaPlaylist,
    choose,
    codec_of,
    download_media,
    fetch_playlist,
    geo_notice,
    language_code,
    mux,
    mux_args,
    parse,
)
from mtdl_web.http import make_client

# Real playlists of 12./13.10.2026 with the url they came from (test/fixtures/README.md).
HLS = Path(__file__).parent / "fixtures" / "hls"
SRF = "https://srf-vod-amd.akamaized.net/world/hls/meteo_geb/2026/10/meteo_geb_20261012_195926_19806811_v_webcast_h264_,q40,q10,q20,q30,q50,q60,.mp4.csmil/index-f5-v1-a1.m3u8"
ORF = "https://apasfiis.sf.apa.at/ipad/cms-worldwide/2026/10/12/2026-10-12_2251_tl_01_ZIB-Flash-4_____14342897__o__1074598808__s16169141_1__ORF1HD_22511911P_22542903P_QXA.mp4/chunklist_b4165000.m3u8"
WDR = "https://wdrvod-rwrtr.akamaized.net/i/,/medp/ondemand/weltweit/fsk0/353/3539543/,3539543_69050203,3539543_69050204,3539543_69050205,3539543_69050202,3539543_69050201,.mp4.csmil/index-f3-v1-a1.m3u8"
ORF_MASTER = "https://vod-ww.mdn.ors.at/cms-worldwide_nas/_definst_/nas/cms-worldwide/online/2026/08/11/2026-08-11_1512_in_06_Universum--Lung_____14334737__o__1944837662__s16134677_Q6A.mp4/playlist.m3u8"
FUNK_MASTER = "https://funk-02.akamaized.net/22679/files/21/02/16/2776328/22679-Kj2Twv7yV8X4tNq.ism/manifest.m3u8"
ARTE_MASTER = "https://manifest-arte.akamaized.net/api/manifest/v1/Generate/fbb1944a-661e-4e8e-a8a8-5712e122f925/de/XQ+KS+CHEV1/127400-010-A.m3u8"


def media(name: str, url: str) -> MediaPlaylist:
    playlist = parse((HLS / name).read_text(), url)
    assert isinstance(playlist, MediaPlaylist)
    return playlist


def master(name: str, url: str) -> MasterPlaylist:
    playlist = parse((HLS / name).read_text(), url)
    assert isinstance(playlist, MasterPlaylist)
    return playlist


def test_media_playlists() -> None:
    srf = media("srf-media.m3u8", SRF)
    assert (len(srf.segments), srf.endlist) == (25, True)
    assert srf.duration == pytest.approx(235.68)  # the film list says 234 s
    first = srf.segments[0].uri
    assert first.startswith(SRF.rsplit("/", 1)[0] + "/segment-1-f5-v1-a1.ts?caption=srf/")
    orf = media("orf-chunklist.m3u8", ORF)
    assert len(orf.segments) == 20
    assert orf.duration == pytest.approx(189.696)  # 189 s in the list
    wdr = media("wdr-media.m3u8", WDR)
    assert all(s.uri.startswith("https://wdrvod-pckgr.akamaized.net/") for s in wdr.segments)  # other host
    assert media("funk-media-30.m3u8", "https://funk-02.akamaized.net/x/a.m3u8").duration == 60


def test_orf_master_with_separate_audio() -> None:
    orf = master("orf-master.m3u8", ORF_MASTER)
    assert len(orf.variants) == 6
    variant, audio = choose(orf, "hd")
    assert (variant.resolution, variant.uri) == (
        (1920, 1080),
        ORF_MASTER.replace("playlist.m3u8", "chunklist_b6100000_vo.m3u8"),
    )
    assert audio is not None and audio.uri == ORF_MASTER.replace(
        "playlist.m3u8", "chunklist_b192000_ao_slde_t64RGV1dHNjaA==.m3u8"
    )
    assert choose(orf, "normal")[0].resolution == (1280, 720)
    assert choose(orf, "low")[0].resolution == (512, 288)


def test_funk_master_skips_audio_only_variants() -> None:
    funk = master("funk-master.m3u8", FUNK_MASTER)
    assert len(funk.variants) == 7  # five video variants, two audio-only
    variant, audio = choose(funk, "hd")
    assert variant.uri.endswith("-audio=152000-video=3968000.m3u8")
    assert audio is None  # audio is muxed into the variant
    assert choose(funk, "low")[0].resolution == (426, 240)


def test_arte_master_in_h264_and_hevc() -> None:
    """ARTE since 10/2026: every resolution twice, HEVC with a slightly higher peak bandwidth. Before the codec
    choice the bandwidth decided and every download came as HEVC."""
    arte = master("arte-master.m3u8", ARTE_MASTER)
    assert sorted((codec_of(v), v.resolution) for v in arte.variants) == [
        ("h264", (768, 432)),
        ("h264", (1280, 720)),
        ("h264", (1920, 1080)),
        ("hevc", (768, 432)),
        ("hevc", (1280, 720)),
        ("hevc", (1920, 1080)),
    ]
    expected = {"hd": "v1080", "normal": "v720", "low": "v432"}
    for quality, size in expected.items():
        for codec, suffix in (("prefer_h264", "h264"), ("h264_only", "h264"), ("prefer_hevc", "h265")):
            variant, audio = choose(arte, quality, codec)  # type: ignore[arg-type]
            assert variant.uri.endswith(f"_{size}_crf_{suffix}.m3u8"), (quality, codec)
            # Five renditions (German, multilingual, French, both in plain language): German is the default.
            assert audio is not None and audio.language == "de" and audio.default
    assert choose(arte, "hd")[0].uri.endswith("_v1080_crf_h264.m3u8")  # H.264 unless told otherwise


def test_codec_choice_leaves_single_codec_masters_alone() -> None:
    """ORF names no codecs (they count as H.264), funk only H.264: every choice gives what the bandwidth gave."""
    for name, url in (("orf-master.m3u8", ORF_MASTER), ("funk-master.m3u8", FUNK_MASTER)):
        playlist = master(name, url)
        for quality in ("hd", "normal", "low"):
            plain = choose(playlist, quality)
            for codec in ("prefer_hevc", "h264_only"):
                assert choose(playlist, quality, codec) == plain


def test_hevc_only_stream_without_hevc() -> None:
    # The ARTE master without its H.264 variants (each one is a tag line plus its uri line).
    lines = (HLS / "arte-master.m3u8").read_text().splitlines()
    text: list[str] = []
    skip = False
    for line in lines:
        if skip:
            skip = False
        elif line.startswith("#EXT-X-STREAM-INF") and "avc1" in line:
            skip = True
        else:
            text.append(line)
    hevc_only = parse("\n".join(text), ARTE_MASTER)
    assert isinstance(hevc_only, MasterPlaylist) and {codec_of(v) for v in hevc_only.variants} == {"hevc"}
    assert choose(hevc_only, "hd")[0].resolution == (1920, 1080)  # preferring H.264 still takes HEVC
    with pytest.raises(DownloadError) as caught:
        choose(hevc_only, "hd", "h264_only")
    assert caught.value.code == "codec"


def test_geo_notice() -> None:
    # Where ORF sends a request from outside Austria (16.11.2026), and two ordinary addresses.
    assert geo_notice(
        "https://apasfiis.sf.apa.at/gp_nas/_definst_/nas/gp/online/insert_geoprotection_hr.smil/playlist.m3u8"
    )
    assert not geo_notice(ORF_MASTER) and not geo_notice(ARTE_MASTER)


def test_language_codes_and_mux_arguments(tmp_path: Path) -> None:
    assert [language_code(x) for x in ("de", "DE", "fr", "de-DE", "mul", "qaa", "xx", None, "")] == [
        "deu",
        "deu",
        "fra",
        "deu",
        "mul",
        "qaa",
        None,
        None,
        None,
    ]
    video, audio, out = tmp_path / "v.m3u8", tmp_path / "a.m3u8", tmp_path / "o.mp4"
    plain = mux_args(video, None, out)
    assert "-tag:v" not in plain and not any(a.startswith("language=") for a in plain)
    assert plain[-1] == str(out) and plain[plain.index("-c") + 1] == "copy"
    hevc = mux_args(video, audio, out, hevc=True, audio_language="de")
    assert hevc[hevc.index("-tag:v") + 1] == "hvc1"
    assert hevc[hevc.index("-metadata:s:a:0") + 1] == "language=deu"
    assert "language=deu" not in mux_args(video, None, out, audio_language="de")  # no separate rendition


def test_not_a_playlist() -> None:
    with pytest.raises(DownloadError):
        parse("<html>404</html>", SRF)


class SegmentServer:
    def __init__(self) -> None:
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        return httpx.Response(200, content=b"segment " + str(request.url).encode())


@pytest.mark.anyio
async def test_download_writes_segments_and_a_local_playlist(tmp_path: Path) -> None:
    server = SegmentServer()
    orf = media("orf-chunklist.m3u8", ORF)
    calls: list[tuple[int, int, int]] = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(server.handler)) as client:
        local = await download_media(client, orf, tmp_path, TokenBucket(), lambda *a: calls.append(a))
    assert len(server.requests) == 20
    assert sorted(p.name for p in tmp_path.glob("*.ts")) == [f"{i:05d}.ts" for i in range(20)]
    assert (tmp_path / "00003.ts").read_bytes() == b"segment " + orf.segments[3].uri.encode()
    text = local.read_text()
    assert text.startswith("#EXTM3U\n") and text.endswith("#EXT-X-ENDLIST\n")
    assert "#EXTINF:9.600,\n00000.ts" in text and "http" not in text  # ffmpeg sees only local files
    assert calls[0] == (0, 20, 0) and calls[-1][:2] == (20, 20)

    # A second run (restart) fetches only what is missing.
    (tmp_path / "00007.ts").unlink()
    server.requests.clear()
    async with httpx.AsyncClient(transport=httpx.MockTransport(server.handler)) as client:
        await download_media(client, orf, tmp_path, TokenBucket(), lambda *a: None)
    assert server.requests == [orf.segments[7].uri]


@pytest.mark.anyio
async def test_a_failed_segment_stops_the_others(tmp_path: Path) -> None:
    """The first segment answers 404 at once, the others take a while: when download_media gives up, no segment
    still loads in the background (it would hold bandwidth and write into the parts a retry appends to)."""
    orf = media("orf-chunklist.m3u8", ORF)
    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if str(request.url) == orf.segments[0].uri:
            return httpx.Response(404)
        await asyncio.sleep(0.2)
        return httpx.Response(200, content=b"segment")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DownloadError):
            await download_media(client, orf, tmp_path, TokenBucket(), lambda *a: None)
        asked = len(requests)
        await asyncio.sleep(0.5)
    # The first batch of four, and at most one more that took the place segment 0 freed; nothing afterwards.
    assert asked <= 5 and len(requests) == asked
    assert not list(tmp_path.glob("*.ts")) and not list(tmp_path.glob("*.part"))


@pytest.mark.anyio
async def test_limits_against_a_broken_source(tmp_path: Path) -> None:
    async def endless(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"#EXTM3U\n" + b"#" * (PLAYLIST_LIMIT + 1))

    async with httpx.AsyncClient(transport=httpx.MockTransport(endless)) as client:
        with pytest.raises(DownloadError) as caught:
            await fetch_playlist(client, "https://cdn.example/huge.m3u8", TokenBucket())
    assert caught.value.code == "format"

    lines = "".join(f"#EXTINF:2,\n{i}.ts\n" for i in range(MAX_SEGMENTS + 1))
    huge = parse(f"#EXTM3U\n#EXT-X-TARGETDURATION:2\n{lines}#EXT-X-ENDLIST\n", "https://cdn.example/a.m3u8")
    assert isinstance(huge, MediaPlaylist)
    with pytest.raises(DownloadError) as caught:
        await download_media(httpx.AsyncClient(), huge, tmp_path, TokenBucket(), lambda *a: None)
    assert caught.value.code == "format" and not list(tmp_path.iterdir())


@pytest.mark.anyio
async def test_urls_into_the_own_network_are_refused(tmp_path: Path) -> None:
    """A manipulated playlist (or film list) must not make mtdl-web fetch from devices in the own network and put
    their answers into the download folder: keys, segments and playlists alike, and without trying again."""
    key = '#EXT-X-KEY:METHOD=AES-128,URI="http://192.168.0.1/cgi-bin/admin?action=reboot"\n'
    for head, segment in (("", "http://10.0.0.5:9200/0.ts"), (key, "https://cdn.example/0.ts")):
        text = f"#EXTM3U\n#EXT-X-TARGETDURATION:2\n{head}#EXTINF:2,\n{segment}\n#EXT-X-ENDLIST\n"
        playlist = parse(text, "https://cdn.example/a.m3u8")
        assert isinstance(playlist, MediaPlaylist)
        async with make_client(public_only=True) as client:
            with pytest.raises(DownloadError) as caught:
                await download_media(client, playlist, tmp_path / "x", TokenBucket(), lambda *a: None)
        assert (caught.value.code, caught.value.retryable) == ("not_public", False)
    async with make_client(public_only=True) as client:
        with pytest.raises(DownloadError) as caught:
            await fetch_playlist(client, "http://169.254.169.254/latest/meta-data/", TokenBucket())
    assert caught.value.code == "not_public"
    assert not [p for p in (tmp_path / "x").iterdir() if p.is_file()]


@pytest.mark.anyio
async def test_live_and_encrypted_playlists(tmp_path: Path) -> None:
    live = (HLS / "orf-chunklist.m3u8").read_text().replace("#EXT-X-ENDLIST", "")
    playlist = parse(live, ORF)
    assert isinstance(playlist, MediaPlaylist)
    with pytest.raises(DownloadError, match="live"):
        await download_media(httpx.AsyncClient(), playlist, tmp_path, TokenBucket(), lambda *a: None)
    # AES-128: the key is fetched like a segment and the local playlist points at it, explicit IV kept.
    text = (
        (HLS / "orf-chunklist.m3u8")
        .read_text()
        .replace(
            "#EXT-X-MEDIA-SEQUENCE:0", '#EXT-X-MEDIA-SEQUENCE:0\n#EXT-X-KEY:METHOD=AES-128,URI="key.php?id=1",IV=0x1234'
        )
    )
    encrypted = parse(text, ORF)
    assert isinstance(encrypted, MediaPlaylist)
    server = SegmentServer()
    async with httpx.AsyncClient(transport=httpx.MockTransport(server.handler)) as client:
        local = await download_media(client, encrypted, tmp_path / "enc", TokenBucket(), lambda *a: None)
    assert server.requests[0] == ORF.rsplit("/", 1)[0] + "/key.php?id=1"
    assert '#EXT-X-KEY:METHOD=AES-128,URI="key0.bin",IV=0x1234' in local.read_text()


@pytest.mark.net
@pytest.mark.anyio
async def test_real_orf_stream_with_separate_audio(tmp_path: Path) -> None:
    """Opt-in (pytest -m net, needs ffmpeg): ORF "ZIB Flash 4" of 12.10.2026, lowest variant plus audio."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg missing")
    url = ORF.replace("chunklist_b4165000.m3u8", "playlist.m3u8")
    bucket = TokenBucket()
    async with make_client() as client:
        text, final_url = await fetch_playlist(client, url, bucket)
        top = parse(text, final_url)
        assert isinstance(top, MasterPlaylist)
        variant, audio = choose(top, "low")
        assert audio is not None and audio.uri is not None
        playlists = []
        for name, uri in (("video", variant.uri), ("audio", audio.uri)):
            text, final_url = await fetch_playlist(client, uri, bucket)
            parsed = parse(text, final_url)
            assert isinstance(parsed, MediaPlaylist)
            playlists.append(await download_media(client, parsed, tmp_path / name, bucket, lambda *a: None))
    output = tmp_path / "zib.mp4"
    await mux(playlists[0], playlists[1], output)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(output)],
        capture_output=True,
        check=True,
        text=True,
    )
    info = json.loads(probe.stdout)
    assert sorted(s["codec_type"] for s in info["streams"]) == ["audio", "video"]
    assert float(info["format"]["duration"]) == pytest.approx(189.7, abs=2)


@pytest.mark.net
@pytest.mark.anyio
async def test_real_arte_stream_as_hevc(tmp_path: Path) -> None:
    """Opt-in (pytest -m net, needs ffmpeg): ARTE "ASEAN-Gipfel: Protest gegen US-KI-Projekt" of 15.11.2026 (104 s),
    the lowest HEVC variant plus the German rendition. ARTE sends hev1; the file says hvc1 and its sound deu."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg missing")
    url = "https://manifest-arte.akamaized.net/api/manifest/v1/Generate/675ecd7a-cc2f-4a76-ab1d-65b2868392eb/de/XQ+KS+CHEV1/136379-000-A.m3u8"
    bucket = TokenBucket()
    async with make_client() as client:
        text, final_url = await fetch_playlist(client, url, bucket)
        top = parse(text, final_url)
        assert isinstance(top, MasterPlaylist)
        variant, audio = choose(top, "low", "prefer_hevc")
        assert codec_of(variant) == "hevc" and audio is not None and audio.uri is not None
        playlists = []
        for name, uri in (("video", variant.uri), ("audio", audio.uri)):
            text, final_url = await fetch_playlist(client, uri, bucket)
            parsed = parse(text, final_url)
            assert isinstance(parsed, MediaPlaylist)
            playlists.append(await download_media(client, parsed, tmp_path / name, bucket, lambda *a: None))
    output = tmp_path / "arte.mp4"
    await mux(playlists[0], playlists[1], output, hevc=True, audio_language=audio.language)
    probe = subprocess.run(
        [
            *("ffprobe", "-v", "error", "-of", "json"),
            *("-show_entries", "format=duration:stream=codec_type,codec_name,codec_tag_string:stream_tags=language"),
            str(output),
        ],
        capture_output=True,
        check=True,
        text=True,
    )
    info = json.loads(probe.stdout)
    streams = {s["codec_type"]: s for s in info["streams"]}
    assert (streams["video"]["codec_name"], streams["video"]["codec_tag_string"]) == ("hevc", "hvc1")
    assert streams["audio"]["tags"]["language"] == "deu"
    assert float(info["format"]["duration"]) == pytest.approx(104, abs=2)
