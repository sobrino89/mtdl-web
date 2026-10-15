"""Limit schedule: a second bandwidth limit for chosen days and hours, e.g. "Monday to Friday from 8 to 18 at
20 Mbit/s, otherwise open". Outside its window the normal limit applies (the switch in the status bar). Inside the
window that switch pauses the schedule until the window ends. Times are wall-clock times in the configured time zone;
a window may run past midnight (22:00 to 06:00), the same start and end mean the whole day.
"""

import dataclasses
import datetime as dt
from typing import Literal

from mtdl_web.state import AppSettings

Source = Literal["normal", "schedule", "paused"]


@dataclasses.dataclass(frozen=True, slots=True)
class Window:
    start: dt.datetime
    end: dt.datetime


@dataclasses.dataclass(frozen=True, slots=True)
class LimitState:
    rate: int  # byte/s, 0 = unlimited
    source: Source
    until: dt.datetime | None  # the next change by the schedule: end of this window or start of the next


def _clock(text: str) -> dt.time:
    hours, minutes = text.split(":")
    return dt.time(int(hours), int(minutes))


def windows(settings: AppSettings, around: dt.datetime) -> list[Window]:
    """The windows that start between the day before ``around`` and a week after it, in order."""
    start, end = _clock(settings.schedule_start), _clock(settings.schedule_end)
    found = []
    for offset in range(-1, 8):
        day = around.date() + dt.timedelta(days=offset)
        if day.weekday() not in settings.schedule_days:
            continue
        end_day = day if end > start else day + dt.timedelta(days=1)  # equal: the whole day
        found.append(
            Window(dt.datetime.combine(day, start, around.tzinfo), dt.datetime.combine(end_day, end, around.tzinfo))
        )
    return found


def limit_state(settings: AppSettings, now: dt.datetime, paused_until: dt.datetime | None = None) -> LimitState:
    """The limit in effect at ``now`` (aware, in the configured time zone)."""
    normal = settings.effective_rate
    if not settings.schedule_enabled or not settings.schedule_days:
        return LimitState(normal, "normal", None)
    found = windows(settings, now)
    current = next((w for w in found if w.start <= now < w.end), None)
    if current is None:
        return LimitState(normal, "normal", min((w.start for w in found if w.start > now), default=None))
    end = current.end
    while following := next((w for w in found if w.start <= end < w.end), None):  # windows that touch
        end = following.end
    if paused_until is not None and now < paused_until:
        return LimitState(normal, "paused", end)
    return LimitState(settings.schedule_limit, "schedule", end)
