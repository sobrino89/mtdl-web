import time
from collections.abc import Callable

from conftest import FULL_EXTRACT, load_films
from fastapi.testclient import TestClient
from test_downloads import api  # noqa: F401 - fixture
from test_filmlist_store import _tokens
from test_subscriptions import REFERENCE, runtime_of

from mtdl_web.models import Film


def wait_done(client: TestClient, download_id: int) -> None:
    deadline = time.monotonic() + 10
    while client.get(f"/api/v1/downloads/{download_id}").json()["state"] != "done":
        assert time.monotonic() < deadline
        time.sleep(0.05)


def test_search_scopes(api: TestClient) -> None:  # noqa: F811
    def total(**params: str) -> int:
        return int(api.get("/api/v1/search", params={"q": "gipfeltreffen", "limit": 1, **params}).json()["total"])

    def expected(text: Callable[[Film], str]) -> int:
        return sum(any(t.startswith("gipfeltreffen") for t in _tokens(text(f))) for f in load_films(FULL_EXTRACT))

    assert total() == expected(lambda f: f.topic + " " + f.title) == 119  # word prefix: not "Aufstiegsgipfeltreffen"
    assert total(scope="title") == expected(lambda f: f.title)
    assert total(scope="topic") == expected(lambda f: f.topic)
    assert total(scope="all") == expected(lambda f: f.topic + " " + f.title + " " + f.description) >= 119


def test_status_per_hit(api: TestClient) -> None:  # noqa: F811
    hits = api.get("/api/v1/search", params={"q": "extra 3 gipfeltreffen", "status": True}).json()["films"]
    ard = next(f for f in hits if f["channel"] == "ARD" and f["title"] == "Das ab- und wieder zugesagte Gipfeltreffen")
    assert ard["status"] == {
        "download_id": None,
        "state": None,
        "progress": None,
        "duplicate": False,
        "subscription_id": None,
        "subscription_name": None,
    }
    created = api.post("/api/v1/downloads", json={"film_id": ard["id"]}).json()
    wait_done(api, created["id"])
    hits = api.get("/api/v1/search", params={"q": "extra 3 gipfeltreffen", "status": True}).json()["films"]
    status = {f["channel"]: f["status"] for f in hits if f["title"] == "Das ab- und wieder zugesagte Gipfeltreffen"}
    assert (status["ARD"]["state"], status["ARD"]["duplicate"]) == ("done", False)
    assert (status["NDR"]["state"], status["NDR"]["duplicate"], status["NDR"]["download_id"]) == (
        "done",
        True,
        created["id"],
    )


def test_status_names_the_matching_subscription(api: TestClient) -> None:  # noqa: F811
    sid = api.post("/api/v1/subscriptions", json={**REFERENCE, "state": "paused"}).json()["id"]
    hits = api.get("/api/v1/search", params={"q": "das gipfeltreffen", "status": True, "limit": 100}).json()["films"]
    named = {f["title"] for f in hits if f["status"]["subscription_id"] == sid}
    assert len(named) == 20 and "Das Beste kommt noch! (58)" in named
    assert {f["status"]["subscription_name"] for f in hits if f["status"]["subscription_id"]} == {"Das Gipfeltreffen"}


def test_aggregated_status_and_limit_switch(api: TestClient) -> None:  # noqa: F811
    status = api.get("/api/v1/status").json()
    assert status["filmlist"]["films"] == 220
    assert status["downloads"] == {"counts": {}, "running": [], "speed": 0.0, "progress": None}
    assert status["limit"] == {
        "bandwidth_limit": 0,
        "bandwidth_enabled": True,
        "speed_unit": "Mbit/s",
        "rate": 0,
        "source": "normal",
        "until": None,
    }
    assert status["problems"] == [] and status["subscriptions"] == 0
    api.patch("/api/v1/settings", json={"bandwidth_limit": 6_250_000})  # 50 Mbit/s
    assert runtime_of(api).bucket.rate == 6_250_000
    api.patch("/api/v1/settings", json={"bandwidth_enabled": False})
    assert runtime_of(api).bucket.rate == 0  # off, the value stays for the switch
    assert api.get("/api/v1/status").json()["limit"]["bandwidth_limit"] == 6_250_000
