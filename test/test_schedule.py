import datetime as dt
import zoneinfo
from pathlib import Path

import pytest
from test_downloads import MediaServer, running_app

from mtdl_web.schedule import limit_state
from mtdl_web.state import AppSettings

BERLIN = zoneinfo.ZoneInfo("Europe/Berlin")
OFFICE = AppSettings(schedule_enabled=True, schedule_limit=2_500_000)  # Mon to Fri 8 to 18 at 20 Mbit/s
OPEN = 0


def at(day: int, hour: int, minute: int = 0) -> dt.datetime:
    """A time in the week of Monday, 12 October 2026 (day 0 = Monday)."""
    return dt.datetime(2026, 10, 12 + day, hour, minute, tzinfo=BERLIN)


def test_weekdays_throttled_weekend_open() -> None:  # a typical schedule
    state = limit_state(OFFICE, at(0, 7, 59))
    assert (state.rate, state.source, state.until) == (OPEN, "normal", at(0, 8))
    state = limit_state(OFFICE, at(0, 8))
    assert (state.rate, state.source, state.until) == (2_500_000, "schedule", at(0, 18))
    state = limit_state(OFFICE, at(2, 17, 59))
    assert (state.rate, state.until) == (2_500_000, at(2, 18))
    state = limit_state(OFFICE, at(4, 18))  # Friday evening: open until Monday morning
    assert (state.rate, state.source, state.until) == (OPEN, "normal", at(7, 8))
    assert limit_state(OFFICE, at(5, 12)).rate == OPEN


def test_the_normal_limit_applies_outside_the_window() -> None:
    settings = OFFICE.model_copy(update={"bandwidth_limit": 6_250_000})  # 50 Mbit/s otherwise
    assert limit_state(settings, at(5, 12)).rate == 6_250_000
    assert limit_state(settings, at(1, 9)).rate == 2_500_000
    off = settings.model_copy(update={"schedule_enabled": False})
    assert (limit_state(off, at(1, 9)).rate, limit_state(off, at(1, 9)).until) == (6_250_000, None)


def test_windows_past_midnight_and_whole_days() -> None:
    night = AppSettings(schedule_enabled=True, schedule_days=[4], schedule_start="22:00", schedule_end="06:00")
    assert limit_state(night, at(5, 3)).source == "schedule"  # Saturday 3:00 belongs to Friday's window
    assert limit_state(night, at(5, 3)).until == at(5, 6)
    assert limit_state(night, at(5, 22)).source == "normal"  # Saturday is not a schedule day
    weekend = AppSettings(schedule_enabled=True, schedule_days=[5, 6], schedule_start="00:00", schedule_end="00:00")
    state = limit_state(weekend, at(5, 12))
    assert (state.source, state.until) == ("schedule", at(7, 0))  # Saturday and Sunday as one window


def test_the_switch_pauses_until_the_window_ends() -> None:
    state = limit_state(OFFICE, at(0, 9), paused_until=at(0, 18))
    assert (state.rate, state.source, state.until) == (OPEN, "paused", at(0, 18))
    assert limit_state(OFFICE, at(1, 9), paused_until=at(0, 18)).source == "schedule"  # the next day it holds again


def test_settings_are_checked() -> None:
    assert AppSettings(schedule_days=[4, 0, 4, 2]).schedule_days == [0, 2, 4]
    for bad in ({"schedule_start": "24:00"}, {"schedule_end": "8:00"}, {"schedule_days": [7]}):
        with pytest.raises(ValueError):
            AppSettings.model_validate(bad)


def test_schedule_and_pause_through_the_api(tmp_path: Path) -> None:
    with running_app(tmp_path, MediaServer()) as api:
        always = {
            "schedule_enabled": True,
            "schedule_days": [0, 1, 2, 3, 4, 5, 6],
            "schedule_start": "00:00",
            "schedule_end": "00:00",
            "schedule_limit": 1_250_000,
        }
        assert api.patch("/api/v1/settings", json=always).status_code == 200
        limit = api.get("/api/v1/status").json()["limit"]
        assert (limit["rate"], limit["source"]) == (1_250_000, "schedule")
        paused = api.put("/api/v1/limit/pause", json={"paused": True}).json()
        assert (paused["rate"], paused["source"]) == (0, "paused")
        assert api.get("/api/v1/status").json()["limit"]["source"] == "paused"
        back = api.put("/api/v1/limit/pause", json={"paused": False}).json()
        assert (back["rate"], back["source"]) == (1_250_000, "schedule")
        assert api.patch("/api/v1/settings", json={"schedule_start": "25:00"}).status_code == 422
        assert api.patch("/api/v1/settings", json={"schedule_enabled": False}).json()["schedule_enabled"] is False
        assert api.get("/api/v1/status").json()["limit"]["source"] == "normal"
