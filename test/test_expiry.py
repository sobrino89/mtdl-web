"""A subscription valid until a day: "Staffel 2 endet in drei Wochen". On its last day it still
loads; afterwards it pauses itself (or is deleted, files and history stay) and says so on the event bus."""

import datetime as dt
from pathlib import Path

from test_downloads import MediaServer, running_app, wait_until_idle
from test_subscriptions import REFERENCE, runtime_of

from mtdl_web.events import Event, SubscriptionExpired


def test_a_subscription_pauses_itself_after_its_last_day(tmp_path: Path) -> None:
    with running_app(tmp_path, MediaServer()) as api:
        runtime = runtime_of(api)
        events: list[Event] = []
        runtime.bus.subscribe(events.append)
        today = dt.datetime.now(runtime.settings.timezone).date()
        yesterday = (today - dt.timedelta(days=1)).isoformat()

        refused = api.post("/api/v1/subscriptions", json={**REFERENCE, "valid_until": yesterday})
        assert (refused.status_code, refused.json()["detail"]["code"]) == (422, "valid_until_past")
        paused = api.post("/api/v1/subscriptions", json={**REFERENCE, "valid_until": yesterday, "state": "paused"})
        assert paused.status_code == 201  # paused, an old day does no harm

        sub = api.post("/api/v1/subscriptions", json={**REFERENCE, "valid_until": today.isoformat()}).json()
        assert (sub["valid_until"], sub["on_expiry"]) == (today.isoformat(), "pause")
        assert api.post(f"/api/v1/subscriptions/{sub['id']}/check").json() == {"queued": 20}  # the last day counts
        wait_until_idle(api)

        runtime.state.db.run("UPDATE subscription SET valid_until = ? WHERE id = ?", (yesterday, sub["id"]))
        assert api.post(f"/api/v1/subscriptions/{sub['id']}/check").json() == {"queued": 0}
        assert api.get(f"/api/v1/subscriptions/{sub['id']}").json()["state"] == "paused"
        (expiry,) = [e for e in events if isinstance(e, SubscriptionExpired)]
        assert (expiry.subscription_id, expiry.valid_until, expiry.action) == (sub["id"], yesterday, "paused")
        assert expiry.subscription_name == "Das Gipfeltreffen"


def test_a_subscription_can_go_after_its_last_day(tmp_path: Path) -> None:
    with running_app(tmp_path, MediaServer()) as api:
        runtime = runtime_of(api)
        events: list[Event] = []
        runtime.bus.subscribe(events.append)
        today = dt.datetime.now(runtime.settings.timezone).date()
        draft = {**REFERENCE, "valid_until": today.isoformat(), "on_expiry": "delete"}
        sub = api.post("/api/v1/subscriptions", json=draft).json()
        api.post(f"/api/v1/subscriptions/{sub['id']}/check")
        wait_until_idle(api)
        last_week = (today - dt.timedelta(days=7)).isoformat()
        runtime.state.db.run("UPDATE subscription SET valid_until = ? WHERE id = ?", (last_week, sub["id"]))
        assert api.post("/api/v1/subscriptions/check").json() == {"checked": 1, "queued": 0}
        assert api.get(f"/api/v1/subscriptions/{sub['id']}").status_code == 404
        assert [e.action for e in events if isinstance(e, SubscriptionExpired)] == ["deleted"]
        history = api.get("/api/v1/downloads", params={"state": "done"}).json()["items"]
        assert len(history) == 20  # files and history stay
