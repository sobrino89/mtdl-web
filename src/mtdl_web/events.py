"""In-process event bus. Webhooks, notifications and hook scripts subscribe here, not inside the modules.

Event names are part of the public API (webhooks): never rename one, add a new one instead.
"""

import asyncio
import datetime as dt
import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import ClassVar

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True, kw_only=True)
class Event:
    name: ClassVar[str]
    at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))


@dataclass(frozen=True, slots=True, kw_only=True)
class FilmlistUpdated(Event):
    name: ClassVar[str] = "filmlist.updated"
    kind: str  # "full" or "diff"
    list_id: str
    created: dt.datetime
    added: int
    changed: int
    removed: int
    total: int


@dataclass(frozen=True, slots=True, kw_only=True)
class SubscriptionExpired(Event):
    """A subscription reached its last day (valid_until) and paused itself or was deleted."""

    name: ClassVar[str] = "subscription.expired"
    subscription_id: int
    subscription_name: str
    valid_until: str  # ISO date, the last day it searched
    action: str  # "paused" or "deleted"


Handler = Callable[[Event], Awaitable[None] | None]


class EventBus:
    def __init__(self) -> None:
        self._handlers: list[Handler] = []

    def subscribe(self, handler: Handler) -> Callable[[], None]:
        """Register a handler for every event; returns a function that unsubscribes it."""
        self._handlers.append(handler)
        return lambda: self._handlers.remove(handler)

    async def publish(self, event: Event) -> None:
        """Call every handler; a failing handler is logged and does not stop the others or the publisher."""
        for handler in list(self._handlers):
            try:
                result = handler(event)
                if inspect.isawaitable(result):
                    await result
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("event handler %r failed for %s", handler, event.name)


@dataclass(frozen=True, slots=True, kw_only=True)
class DownloadEvent(Event):
    download_id: int
    film_id: str  # as a string, like the API: 63-bit ids do not fit a JavaScript number
    channel: str
    topic: str
    title: str
    path: str  # relative to the library (library_id), else to the download folder
    subscription_id: int | None
    file: str = ""  # the video as the container sees it, absolute
    library_id: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class DownloadQueued(DownloadEvent):
    name: ClassVar[str] = "download.queued"


@dataclass(frozen=True, slots=True, kw_only=True)
class DownloadStarted(DownloadEvent):
    name: ClassVar[str] = "download.started"


@dataclass(frozen=True, slots=True, kw_only=True)
class DownloadCompleted(DownloadEvent):
    name: ClassVar[str] = "download.completed"
    size: int
    subtitle_path: str | None
    subtitle_file: str | None = None  # absolute, like ``file``


@dataclass(frozen=True, slots=True, kw_only=True)
class DownloadFailed(DownloadEvent):
    name: ClassVar[str] = "download.failed"
    error: str
    will_retry: bool
