import datetime as dt
import lzma
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from conftest import DIFF_EXTRACT_2019, DIFF_EXTRACT_2133, FIXTURES, FULL_EXTRACT

from mtdl_web import USER_AGENT
from mtdl_web.events import Event, EventBus, FilmlistUpdated
from mtdl_web.filmlist.fetch import SERVER_DIRECTORY, Backoff, FetchError, ListFetcher, choose_kind, servers
from mtdl_web.filmlist.parser import open_filmlist
from mtdl_web.filmlist.store import FilmStore, ImportResult
from mtdl_web.filmlist.updater import FilmlistUpdater
from mtdl_web.http import make_client

UTC = dt.UTC


def at(day: int, hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime(2026, 10, day, hour, minute, tzinfo=UTC)


def imported(created: dt.datetime) -> ImportResult:
    return ImportResult("full", "x", created, 0, 0, 0, 0, 0.0)


@pytest.mark.parametrize(
    ("created", "now", "kind"),
    [
        (at(12, 20, 19), at(12, 21, 40), "diff"),  # after today's 07:00 UTC base
        (at(12, 20, 19), at(13, 6, 30), "full"),  # new day: base 13.10. 07:00 lies ahead, like the Java client
        (at(12, 20, 19), at(13, 7, 30), "full"),
        (at(13, 8, 0), at(13, 9, 0), "diff"),
    ],
)
def test_choose_kind(created: dt.datetime, now: dt.datetime, kind: str) -> None:
    assert choose_kind(imported(created), now) == kind


def test_empty_index_needs_the_full_list() -> None:
    assert choose_kind(None, at(12, 21)) == "full"


class FakeServer:
    """liste.mediathekview.de as far as we use it: two files with ETag, 304 on a matching If-None-Match."""

    def __init__(self) -> None:
        self.files: dict[str, tuple[bytes, str]] = {}
        self.requests: list[httpx.Request] = []
        self.fail_with: httpx.Response | None = None

    def put(self, name: str, source: Path, etag: str) -> None:
        self.files[name] = (lzma.compress(source.read_bytes()), etag)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail_with is not None:
            return self.fail_with
        body, etag = self.files[request.url.path.lstrip("/")]
        if request.headers.get("if-none-match") == etag:
            return httpx.Response(304)
        headers = {"etag": etag, "last-modified": "Mon, 12 Oct 2026 20:45:05 GMT"}
        return httpx.Response(200, content=body, headers=headers)


@pytest.fixture
def server() -> FakeServer:
    s = FakeServer()
    s.put("Filmliste-akt.xz", FULL_EXTRACT, '"akt-2019"')
    s.put("Filmliste-diff.xz", DIFF_EXTRACT_2019, '"diff-2019"')
    return s


@pytest.fixture
def store(tmp_path: Path) -> Iterator[FilmStore]:
    with FilmStore(tmp_path / "films.sqlite", index_description=True) as s:
        yield s


@pytest.mark.anyio
async def test_fetch_sends_user_agent_and_revalidates(tmp_path: Path, server: FakeServer) -> None:
    async with make_client(httpx.MockTransport(server.handler)) as client:
        fetcher = ListFetcher(client, tmp_path / "lists")
        first = await fetcher.fetch("full")
        second = await fetcher.fetch("full")
    assert first.modified
    assert not second.modified
    assert first.path == second.path
    assert lzma.decompress(first.path.read_bytes()) == FULL_EXTRACT.read_bytes()
    initial, revalidation = server.requests
    assert initial.headers["user-agent"] == USER_AGENT
    assert USER_AGENT.startswith("mtdl-web/") and "(+https://github.com/sobrino89/mtdl-web)" in USER_AGENT
    assert "if-none-match" not in initial.headers
    assert revalidation.headers["if-none-match"] == '"akt-2019"'
    assert revalidation.headers["if-modified-since"] == "Mon, 12 Oct 2026 20:45:05 GMT"


@pytest.mark.anyio
async def test_progress_of_download_and_import(
    tmp_path: Path, server: FakeServer, store: FilmStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """For the progress bar of the first start: bytes against the size the server names, then films read."""
    monkeypatch.setattr("mtdl_web.filmlist.store.PROGRESS_EVERY", 50)
    size = len(server.files["Filmliste-akt.xz"][0])
    downloaded: list[tuple[int, int | None]] = []
    async with make_client(httpx.MockTransport(server.handler)) as client:
        fetched = await ListFetcher(client, tmp_path / "lists").fetch("full", lambda d, t: downloaded.append((d, t)))
    assert downloaded[-1] == (size, size) and all(t == size for _, t in downloaded)
    films: list[int] = []
    with open_filmlist(fetched.path) as reader:
        store.import_full(reader, progress=films.append)
    assert films == [50, 100, 150, 200, 220]  # every PROGRESS_EVERY and at the end


@pytest.mark.anyio
async def test_fetch_errors_keep_the_previous_file(tmp_path: Path, server: FakeServer) -> None:
    async with make_client(httpx.MockTransport(server.handler)) as client:
        fetcher = ListFetcher(client, tmp_path / "lists")
        good = await fetcher.fetch("diff")
        server.fail_with = httpx.Response(503, headers={"retry-after": "120"})
        with pytest.raises(FetchError) as caught:
            await fetcher.fetch("diff")
    assert caught.value.retry_after == 120
    assert lzma.decompress(good.path.read_bytes()) == DIFF_EXTRACT_2019.read_bytes()
    assert not (tmp_path / "lists" / "Filmliste-diff.xz.part").exists()


@pytest.mark.anyio
async def test_the_server_directory_names_the_list(tmp_path: Path, server: FakeServer) -> None:
    """Like the Java client: the URL from res.mediathekview.de (real files of 13.10.2026), read once a day."""
    asked: list[str] = []
    directory = {
        "/akt.xml": "res-mediathekview-akt-2026-10-13.xml",
        "/diff.xml": "res-mediathekview-diff-2026-10-13.xml",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        if request.url.host == "res.mediathekview.de":
            return httpx.Response(200, content=(FIXTURES / directory[request.url.path]).read_bytes())
        return server.handler(request)

    async with make_client(httpx.MockTransport(handler)) as client:
        fetcher = ListFetcher(client, tmp_path / "lists", server_directory=SERVER_DIRECTORY)
        await fetcher.fetch("full")
        await fetcher.fetch("full")
    assert asked == [
        "https://res.mediathekview.de/akt.xml",
        "https://liste.mediathekview.de/Filmliste-akt.xz",
        "https://liste.mediathekview.de/Filmliste-akt.xz",
    ]
    assert servers(
        b"<Mediathek><Server><URL>https://b/x.xz</URL><Prio>2</Prio></Server>"
        b"<Server><URL>https://a/x.xz</URL><Prio>1</Prio></Server>"
        b"<Server><URL>http://plain/x.xz</URL><Prio>0</Prio></Server></Mediathek>"
    ) == ["https://a/x.xz", "https://b/x.xz"]


@pytest.mark.anyio
async def test_an_unreadable_directory_falls_back_to_the_official_address(tmp_path: Path, server: FakeServer) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "res.mediathekview.de":
            return httpx.Response(503)
        return server.handler(request)

    async with make_client(httpx.MockTransport(handler)) as client:
        fetcher = ListFetcher(client, tmp_path / "lists", server_directory=SERVER_DIRECTORY)
        assert await fetcher.url("diff") == "https://liste.mediathekview.de/Filmliste-diff.xz"
        assert (await fetcher.fetch("diff")).modified


@pytest.mark.anyio
async def test_network_error_becomes_fetch_error(tmp_path: Path) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with make_client(httpx.MockTransport(refuse)) as client:
        with pytest.raises(FetchError):
            await ListFetcher(client, tmp_path).fetch("full")


def test_backoff_doubles_up_to_the_maximum_and_honours_retry_after() -> None:
    backoff = Backoff(base=60, maximum=600)
    now = at(12, 21)
    delays = [(backoff.failed(now) - now).total_seconds() for _ in range(6)]
    assert delays == [60, 120, 240, 480, 600, 600]
    assert not backoff.ready(now)
    assert (backoff.failed(now, retry_after=3600) - now).total_seconds() == 3600
    backoff.succeeded()
    assert backoff.ready(now) and backoff.failures == 0


@pytest.mark.anyio
async def test_update_rounds(tmp_path: Path, server: FakeServer, store: FilmStore) -> None:
    clock = [at(12, 21, 0)]
    events: list[Event] = []
    bus = EventBus()
    bus.subscribe(events.append)
    async with make_client(httpx.MockTransport(server.handler)) as client:
        updater = FilmlistUpdater(store, ListFetcher(client, tmp_path / "lists"), bus, clock=lambda: clock[0])

        # Empty index: full list.
        result = await updater.update()
        assert result is not None
        assert (result.kind, result.added, result.total) == ("full", 220, 220)
        assert updater.progress is None  # only while an update runs

        # The diff of the same generation adds nothing the full list did not have: skipped.
        clock[0] = at(12, 21, 10)
        assert await updater.update() is None

        # Next generation on the server.
        server.put("Filmliste-diff.xz", DIFF_EXTRACT_2133, '"diff-2133"')
        clock[0] = at(12, 21, 40)
        result = await updater.update()
        assert result is not None
        assert (result.kind, result.added, result.changed, result.removed) == ("diff", 11, 8, 0)

        # Unchanged on the server: 304, nothing to do.
        assert await updater.update() is None
        assert server.requests[-1].headers["if-none-match"] == '"diff-2133"'

        # Server trouble: the error surfaces once, then the updater waits instead of hammering.
        server.fail_with = httpx.Response(503)
        with pytest.raises(FetchError):
            await updater.update()
        requests = len(server.requests)
        clock[0] += dt.timedelta(seconds=30)
        assert await updater.update() is None
        assert len(server.requests) == requests
        server.fail_with = None
        clock[0] += dt.timedelta(seconds=60)
        assert await updater.update() is None  # 304 again, backoff cleared
        assert updater.backoff.failures == 0

    assert [type(e) for e in events] == [FilmlistUpdated, FilmlistUpdated]
    full, diff = events
    assert isinstance(full, FilmlistUpdated) and isinstance(diff, FilmlistUpdated)
    assert (full.name, full.kind, full.added, full.list_id) == (
        "filmlist.updated",
        "full",
        220,
        "b1587d20c3b4f75cdbbab50899244d2d",
    )
    assert (diff.kind, diff.added, diff.changed, diff.created) == ("diff", 11, 8, at(12, 21, 33))


@pytest.mark.anyio
async def test_failing_event_handler_does_not_stop_others() -> None:
    bus = EventBus()
    seen: list[str] = []

    def broken(event: Event) -> None:
        raise RuntimeError("boom")

    async def collect(event: Event) -> None:
        seen.append(event.name)

    bus.subscribe(broken)
    unsubscribe = bus.subscribe(collect)
    event = FilmlistUpdated(kind="diff", list_id="x", created=at(12, 21), added=0, changed=0, removed=0, total=0)
    await bus.publish(event)
    unsubscribe()
    await bus.publish(event)
    assert seen == ["filmlist.updated"]
