import dataclasses
import datetime as dt
import json
import zoneinfo
from pathlib import Path

from conftest import FIXTURES
from test_downloads import MediaServer, running_app
from test_subscriptions import runtime_of

from mtdl_web.downloads.queue import film_from_json
from mtdl_web.episodes import episode_key, same_file
from mtdl_web.models import Film, SearchResult
from mtdl_web.subscriptions import _representatives

BERLIN = zoneinfo.ZoneInfo("Europe/Berlin")
# Real entries of the film list (13.10.2026): ZDF and 3sat list "Die Anstalt" S2026/E08 under different titles with
# one video file; E07 ("Die AntifAnstalt 100") is another episode.
ANSTALT = [
    film_from_json(json.dumps(f))
    for f in json.loads((FIXTURES / "films-die-anstalt-2026-10-13.json").read_text(encoding="utf-8"))
]


def episode(channel: str, number: str) -> Film:
    return next(f for f in ANSTALT if f.channel == channel and f.title.endswith(f"({number})"))


SAT_E08 = episode("3Sat", "S2026/E08")
ZDF_E08 = episode("ZDF", "S2026/E08")
ZDF_E07 = episode("ZDF", "S2026/E07")
TOPIC = {"field": "topic", "operator": "is", "value": "Die Anstalt"}


def test_one_file_on_two_channels_is_one_broadcast() -> None:
    assert episode_key(SAT_E08) != episode_key(ZDF_E08)  # the titles differ: the key alone misses it
    assert same_file(SAT_E08, ZDF_E08)
    assert not same_file(ZDF_E08, ZDF_E07)
    a_week_later = dataclasses.replace(SAT_E08, timestamp=(SAT_E08.timestamp or 0) + 7 * 86400)
    assert not same_file(a_week_later, ZDF_E08)  # an address kept for the "newest episode" is another broadcast


def test_the_home_channel_then_the_longer_title_stands_for_the_broadcast() -> None:
    chosen = _representatives([SAT_E08, ZDF_E08, ZDF_E07], BERLIN)  # ZDF has two hits on the topic, 3sat one
    assert chosen[SAT_E08.id] == chosen[ZDF_E08.id] == ZDF_E08 and chosen[ZDF_E07.id] == ZDF_E07
    chosen = _representatives([SAT_E08, ZDF_E08], BERLIN)  # one hit each: the title naming the episode wins
    assert chosen[SAT_E08.id] == ZDF_E08
    described = dataclasses.replace(ZDF_E08, id=1, title=ZDF_E08.title + " (Audiodeskription)")
    chosen = _representatives(
        [described, ZDF_E08], BERLIN
    )  # longer, but a special version: the plain one names the file
    assert chosen[described.id] == ZDF_E08


@dataclasses.dataclass
class Stub:
    """A source that finds exactly these films."""

    films: list[Film]
    name: str = "stub"
    description_search: bool = False

    async def search(self, query: str, **options: object) -> SearchResult:
        return SearchResult(self.films, len(self.films))

    async def new_since(self, since: dt.datetime, *, limit: int = 1000) -> list[Film]:
        return []


def test_a_subscription_loads_the_file_once_and_knows_it_later(tmp_path: Path) -> None:
    with running_app(tmp_path, MediaServer()) as api:
        runtime = runtime_of(api)
        draft = {"name": "Die Anstalt", "query": "die anstalt", "conditions": [TOPIC]}
        runtime.subscriptions._source = lambda: Stub(ANSTALT)
        items = api.post("/api/v1/subscriptions/preview", json=draft).json()["items"]
        status = {(i["film"]["channel"], i["film"]["title"][-11:]): i["status"] for i in items}
        assert status == {
            ("ZDF", "(S2026/E08)"): "load",
            ("3Sat", "(S2026/E08)"): "duplicate",
            ("ZDF", "(S2026/E07)"): "load",
        }
        assert next(i for i in items if i["status"] == "duplicate")["duplicate_of"] == str(ZDF_E08.id)

        # The ZDF copy was loaded before; later only the 3sat one turns up: nothing to load.
        runtime.downloads.queue.add_done(ZDF_E08, episode_key=episode_key(ZDF_E08), finished_at=0)
        runtime.subscriptions._source = lambda: Stub([SAT_E08])
        later = api.post("/api/v1/subscriptions/preview", json=draft).json()["items"]
        assert [i["status"] for i in later] == ["exists"]
        assert api.get("/api/v1/search", params={"q": "zzz"}).status_code == 200  # the app stays usable
