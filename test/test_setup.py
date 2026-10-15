"""The guide of the first start (Ersteinrichtung): a fresh installation gets it, with the password as its first
step (``first_run`` in the open sign-in status); finishing or skipping ends it. Installations already in use (from
before the guide existed) never see it."""

from pathlib import Path

from test_auth import app
from test_subscriptions import REFERENCE, runtime_of


def test_a_fresh_installation_gets_the_guide_once(tmp_path: Path) -> None:
    with app(tmp_path, auth="password") as api:
        status = api.get("/api/v1/auth/status").json()
        assert (status["setup_required"], status["first_run"]) == (True, True)  # the password comes first
    with app(tmp_path) as api:
        assert api.get("/api/v1/settings").json()["setup_done"] is False
        assert api.patch("/api/v1/settings", json={"setup_done": True}).json()["setup_done"] is True
        assert api.get("/api/v1/auth/status").json()["first_run"] is False


def test_installations_in_use_skip_the_guide(tmp_path: Path) -> None:
    with app(tmp_path) as api:
        assert api.post("/api/v1/subscriptions", json=REFERENCE).status_code == 201
        runtime_of(api).state.update_settings({"setup_done": False})  # as stored before the guide existed
    with app(tmp_path) as api:
        assert api.get("/api/v1/auth/status").json()["first_run"] is False
        assert api.get("/api/v1/settings").json()["setup_done"] is True
