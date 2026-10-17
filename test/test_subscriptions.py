import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import FULL_EXTRACT, load_films
from fastapi.testclient import TestClient
from test_downloads import MediaServer, api, running_app, wait_until_idle  # noqa: F401 - api is a fixture

from mtdl_web.events import FilmlistUpdated
from mtdl_web.runtime import Runtime


def runtime_of(client: TestClient) -> Runtime:
    runtime: Runtime = getattr(client.app, "state").runtime  # noqa: B009 - the ASGI app type does not declare it
    return runtime


def call[T](client: TestClient, function: Callable[[], Awaitable[T]]) -> T:
    assert client.portal is not None
    return client.portal.call(function)


REFERENCE: dict[str, Any] = {
    "name": "Das Gipfeltreffen",
    "query": "das gipfeltreffen",
    "conditions": [
        {"field": "topic", "operator": "is", "value": "Das Gipfeltreffen"},
        {"field": "title", "operator": "is", "value": r"\(\d+\)$", "regex": True},
    ],
    "naming": {"scheme": "episode_number"},
}
WERWOELFE: dict[str, Any] = {
    "name": "Werwölfe",
    "query": "werwölfe",
    "conditions": [
        {"field": "topic", "operator": "is", "value": "Werwölfe · Das Spiel von List und Täuschung"},
        {"field": "channel", "operator": "any_of", "value": ["BR"]},
        {"field": "title", "operator": "is", "value": r"\((S\d+/E)?\d+\)$", "regex": True},
    ],
    "naming": {"scheme": "season_episode", "show": "Werwölfe"},
    "state": "paused",
}


def test_preview_of_the_reference_case(api: TestClient) -> None:  # noqa: F811
    preview = api.post("/api/v1/subscriptions/preview", json=REFERENCE).json()
    assert preview["total"] == 35
    steps = [(s["before"], s["excluded"], s["remaining"], s["kind"]) for s in preview["steps"]]
    # special versions last: none among the hits that the conditions leave
    assert steps == [(35, 10, 25, "condition"), (25, 5, 20, "condition"), (20, 0, 20, "special_versions")]
    assert preview["counts"] == {"excluded": 15, "load": 20}
    load = [i for i in preview["items"] if i["status"] == "load"]
    assert load[0]["path"] == "Das Gipfeltreffen/Season 01/Das Gipfeltreffen - S01E39 - Menschenjagd in gut.mp4"
    assert load[-1]["path"].endswith("S01E58 - Das Beste kommt noch!.mp4")  # oldest first
    best_of = next(i for i in preview["items"] if i["film"]["title"] == "Das Gipfeltreffen – Best of 2025")
    assert (best_of["status"], best_of["excluded_by"]) == ("excluded", 1)
    assert best_of["results"][0] == {"passed": True, "code": "ok", "actual": "Das Gipfeltreffen", "span": [0, 17]}
    assert best_of["results"][1]["passed"] is False
    other = next(i for i in preview["items"] if i["film"]["topic"] == "Kabarett & Comedy")
    assert other["excluded_by"] == 0


def test_rule_errors_point_at_the_condition(api: TestClient) -> None:  # noqa: F811
    broken = {
        **REFERENCE,
        "conditions": [
            *REFERENCE["conditions"],
            {"field": "title", "operator": "is", "value": r"(\d+$", "regex": True},
        ],
    }
    response = api.post("/api/v1/subscriptions/preview", json=broken)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert (detail["code"], detail["condition"], detail["params"]["position"]) == ("invalid_regex", 2, 5)
    assert api.post("/api/v1/subscriptions", json={**REFERENCE, "name": " "}).json()["detail"] == {
        "code": "name_missing"
    }


def test_create_check_and_check_again(api: TestClient) -> None:  # noqa: F811
    created = api.post("/api/v1/subscriptions", json=REFERENCE)
    assert created.status_code == 201
    sid = created.json()["id"]
    assert api.post(f"/api/v1/subscriptions/{sid}/check").json() == {"queued": 20}
    assert api.post(f"/api/v1/subscriptions/{sid}/check").json() == {"queued": 0}
    preview = api.post("/api/v1/subscriptions/preview", json=REFERENCE).json()
    assert preview["counts"] == {"excluded": 15, "exists": 20}
    downloads = api.get("/api/v1/downloads", params={"limit": 100}).json()["items"]
    assert len(downloads) == 20 and {d["subscription_id"] for d in downloads} == {sid}
    listed = api.get("/api/v1/subscriptions").json()
    assert [s["name"] for s in listed] == ["Das Gipfeltreffen"]
    assert listed[0]["last_queued"] == 0 and listed[0]["last_error"] is None


def test_duplicates_on_other_channels_and_livestreams(api: TestClient) -> None:  # noqa: F811
    extra3 = {"query": "das gipfeltreffen", "conditions": [{"field": "topic", "operator": "is", "value": "extra 3"}]}
    preview = api.post("/api/v1/subscriptions/preview", json=extra3).json()
    assert preview["counts"] == {"excluded": 33, "load": 1, "duplicate": 1}
    first = next(i for i in preview["items"] if i["status"] == "load")
    second = next(i for i in preview["items"] if i["status"] == "duplicate")
    assert second["duplicate_of"] == first["film"]["id"]
    assert {first["film"]["channel"], second["film"]["channel"]} == {"ARD", "NDR"}
    streams = api.post("/api/v1/subscriptions/preview", json={"query": "livestream"}).json()
    assert {i["status"] for i in streams["items"] if i["film"]["topic"] == "Livestream"} == {"unavailable"}


def test_paused_subscriptions_wait(api: TestClient) -> None:  # noqa: F811
    werwoelfe = api.post("/api/v1/subscriptions", json=WERWOELFE).json()
    assert werwoelfe["state"] == "paused"
    preview = api.post("/api/v1/subscriptions/preview", json=WERWOELFE).json()
    assert preview["counts"]["load"] == 24  # S01 and S02, E01 to E12
    assert any(
        p.endswith("Werwoelfe - S02E03 - Folge 3 - Was fuer ein Theater.mp4")
        for p in [i["path"] or "" for i in preview["items"]]
    )
    assert api.post("/api/v1/subscriptions/check").json() == {"checked": 0, "queued": 0}  # nothing active
    assert api.get("/api/v1/downloads").json()["counts"] == {}
    resumed = api.put(f"/api/v1/subscriptions/{werwoelfe['id']}", json={**WERWOELFE, "state": "active"}).json()
    assert resumed["state"] == "active"
    assert api.post("/api/v1/subscriptions/check").json() == {"checked": 1, "queued": 24}
    assert api.delete(f"/api/v1/subscriptions/{werwoelfe['id']}").status_code == 204
    assert api.get(f"/api/v1/subscriptions/{werwoelfe['id']}").status_code == 404


def test_film_list_updates_trigger_checks(api: TestClient) -> None:  # noqa: F811
    sid = api.post("/api/v1/subscriptions", json=REFERENCE).json()["id"]
    runtime = runtime_of(api)
    last = runtime.films.last_import()
    assert last is not None
    event = FilmlistUpdated(kind="diff", list_id="x", created=last.created, added=3, changed=0, removed=0, total=3)

    async def publish_and_wait() -> None:
        await runtime.bus.publish(event)
        while runtime.subscriptions.checking:
            await asyncio.sleep(0.01)

    call(api, publish_and_wait)
    assert api.get(f"/api/v1/subscriptions/{sid}").json()["last_queued"] == 20


@pytest.mark.parametrize("scheme", ["episode_number", "date"])
def test_naming_in_the_preview_follows_the_setting(api: TestClient, scheme: str) -> None:  # noqa: F811
    api.patch("/api/v1/settings", json={"normalize_filenames": False})
    werwoelfe = {**WERWOELFE, "naming": {"scheme": scheme}}
    paths = [i["path"] for i in api.post("/api/v1/subscriptions/preview", json=werwoelfe).json()["items"] if i["path"]]
    assert all(p.startswith("Werwölfe · Das Spiel von List und Täuschung/") for p in paths)


def test_failed_and_cancelled_episodes_are_not_queued_again(tmp_path: Path) -> None:
    # A gone episode would fail on every check, a cancelled one would come back: both count as handled.
    (gone,) = [f for f in load_films(FULL_EXTRACT) if f.title == "Menschenjagd in gut (39)"]
    media = MediaServer()
    media.status[gone.url_hd or ""] = 404
    media.delay = 0.05
    with running_app(tmp_path, media) as client:
        sid = client.post("/api/v1/subscriptions", json=REFERENCE).json()["id"]
        assert client.post(f"/api/v1/subscriptions/{sid}/check").json() == {"queued": 20}
        items = client.get("/api/v1/downloads", params={"limit": 100}).json()["items"]
        newest = next(d for d in items if d["film"]["title"] == "Das Beste kommt noch! (58)")
        assert client.post(f"/api/v1/downloads/{newest['id']}/cancel").json()["state"] == "cancelled"
        media.delay = 0
        assert wait_until_idle(client) == {"done": 18, "failed": 1, "cancelled": 1}
        (failed,) = client.get("/api/v1/downloads", params={"state": "failed"}).json()["items"]
        assert (failed["film"]["title"], failed["error_code"], failed["attempts"]) == (gone.title, "gone", 1)

        assert client.post(f"/api/v1/subscriptions/{sid}/check").json() == {"queued": 0}
        preview = client.post("/api/v1/subscriptions/preview", json=REFERENCE).json()
        assert preview["counts"] == {"excluded": 15, "exists": 18, "failed": 1, "cancelled": 1}
        hits = client.get("/api/v1/search", params={"q": "das gipfeltreffen", "status": True}).json()["films"]
        states = {h["title"]: h["status"]["state"] for h in hits}
        assert (states[gone.title], states["Das Beste kommt noch! (58)"]) == ("failed", "cancelled")

        # Removing the entry lets the subscription try again.
        assert client.delete(f"/api/v1/downloads/{newest['id']}").status_code == 204
        assert client.post(f"/api/v1/subscriptions/{sid}/check").json() == {"queued": 1}
        assert wait_until_idle(client) == {"done": 19, "failed": 1}


def test_only_future_episodes(api: TestClient) -> None:  # noqa: F811
    created = api.post("/api/v1/subscriptions", json={**REFERENCE, "skip_existing": True})
    assert created.status_code == 201
    sub = created.json()
    assert sub["stats"] == {"skipped": 20} and "skip_existing" not in sub
    preview = api.post("/api/v1/subscriptions/preview", params={"subscription": sub["id"]}, json=REFERENCE).json()
    assert preview["counts"] == {"excluded": 15, "skipped": 20}
    draft = api.post("/api/v1/subscriptions/preview", json=REFERENCE).json()  # a draft of a new subscription
    assert draft["counts"] == {"excluded": 15, "load": 20}
    assert api.post(f"/api/v1/subscriptions/{sub['id']}/check").json() == {"queued": 0}

    # Saving changed rules with "only future episodes" again leaves the newly matching ones alone, too.
    widened = {**REFERENCE, "conditions": REFERENCE["conditions"][:1], "skip_existing": True}
    assert api.put(f"/api/v1/subscriptions/{sub['id']}", json=widened).json()["stats"] == {"skipped": 25}
    assert api.delete(f"/api/v1/subscriptions/{sub['id']}/skipped").json() == {"released": 25}
    assert api.post(f"/api/v1/subscriptions/{sub['id']}/check").json() == {"queued": 25}
    assert api.delete(f"/api/v1/subscriptions/{sub['id']}").status_code == 204
    assert api.delete(f"/api/v1/subscriptions/{sub['id']}/skipped").status_code == 404
