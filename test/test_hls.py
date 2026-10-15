import json
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from mtdl_web.downloads import DownloadError
from mtdl_web.downloads.bandwidth import TokenBucket
from mtdl_web.downloads.hls import MasterPlaylist, MediaPlaylist, choose, download_media, fetch_playlist, mux, parse
from mtdl_web.http import make_client

# Real playlists of 12./13.10.2026 with the url they came from (test/fixtures/README.md).
HLS = Path(__file__).parent / "fixtures" / "hls"
SRF = "https://srf-vod-amd.akamaized.net/world/hls/meteo_geb/2026/10/meteo_geb_20261012_195926_19806811_v_webcast_h264_,q40,q10,q20,q30,q50,q60,.mp4.csmil/index-f5-v1-a1.m3u8"
ORF = "https://apasfiis.sf.apa.at/ipad/cms-worldwide/2026/10/12/2026-10-12_2251_tl_01_ZIB-Flash-4_____14342897__o__1074598808__s16169141_1__ORF1HD_22511911P_22542903P_QXA.mp4/chunklist_b4165000.m3u8"
WDR = "https://wdrvod-rwrtr.akamaized.net/i/,/medp/ondemand/weltweit/fsk0/353/3539543/,3539543_69050203,3539543_69050204,3539543_69050205,3539543_69050202,3539543_69050201,.mp4.csmil/index-f3-v1-a1.m3u8"
ORF_MASTER = "https://vod-ww.mdn.ors.at/cms-worldwide_nas/_definst_/nas/cms-worldwide/online/2026/08/11/2026-08-11_1512_in_06_Universum--Lung_____14334737__o__1944837662__s16134677_Q6A.mp4/playlist.m3u8"
FUNK_MASTER = "https://funk-02.akamaized.net/22679/files/21/02/16/2776328/22679-Kj2Twv7yV8X4tNq.ism/manifest.m3u8"


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
