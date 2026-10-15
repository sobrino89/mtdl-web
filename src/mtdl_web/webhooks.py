"""Webhooks: events from the bus as POST requests to configured URLs (Home Assistant, n8n, Sonarr, own scripts).

Default body: ``{"event": "download.completed", "time": "…Z", "data": {…}}``, the fields of the event (``events.py``;
event names are public API). A body template replaces it for receivers with their own format, e.g. Sonarr's
``{"name": "DownloadedEpisodesScan", "path": "/downloads/{{data.path}}"}``. Placeholders: ``{{event}}``,
``{{time}}``, ``{{data.<field>}}``. In a JSON body a placeholder inside a string is filled in JSON-escaped, one
standing alone becomes a JSON value (``"size": {{data.size}}``); with an own ``Content-Type`` that is not JSON
(``text/plain`` for ntfy) values are inserted as plain text. Own headers carry API keys.

With a secret the body is signed: ``X-Mtdl-Signature: sha256=<HMAC-SHA256(secret, body)>``. Delivery is best effort:
three attempts (now, after 5 s, after 30 s); the last result is stored per webhook and shown in the settings. Secret
and header values never leave the server again: the API only names what is set.
"""

import asyncio
import contextlib
import dataclasses
import datetime as dt
import hashlib
import hmac
import json
import logging
import re
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, get_args
from urllib.parse import urlsplit

import httpx

from mtdl_web.events import (
    DownloadCompleted,
    DownloadFailed,
    DownloadQueued,
    DownloadStarted,
    Event,
    EventBus,
    FilmlistUpdated,
    SubscriptionExpired,
)
from mtdl_web.state import StateStore

log = logging.getLogger(__name__)

EventName = Literal[
    "filmlist.updated",
    "download.queued",
    "download.started",
    "download.completed",
    "download.failed",
    "subscription.expired",
]
EVENTS: tuple[EventName, ...] = get_args(EventName)
EVENT_TYPES: tuple[type[Event], ...] = (
    FilmlistUpdated,
    DownloadQueued,
    DownloadStarted,
    DownloadCompleted,
    DownloadFailed,
    SubscriptionExpired,
)
TEST_EVENT = "webhook.test"
TIMEOUT = httpx.Timeout(10.0)

PLACEHOLDER = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")
# Every field a template may use; a field the event at hand lacks is empty (or null as a JSON value).
TEMPLATE_FIELDS = frozenset(
    {"event", "time", "data.webhook_id"}
    | {f"data.{f.name}" for cls in EVENT_TYPES for f in dataclasses.fields(cls) if f.name != "at"}
)
MAX_TEMPLATE = 10_000
MAX_HEADERS = 20
HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,100}")  # used with fullmatch: "$" allows a final "\n"
# Printable ASCII, no whitespace at either end (the HTTP client refuses that; the API trims it).
HEADER_VALUE = re.compile(r"(?:[\x21-\x7e](?:[\t\x20-\x7e]{0,1998}[\x21-\x7e])?)?")
# Set by the HTTP client or by mtdl-web itself.
RESERVED_HEADERS = frozenset(
    {"host", "content-length", "transfer-encoding", "connection", "x-mtdl-event", "x-mtdl-signature"}
)

_COLUMNS = (
    "id, url, events, secret, enabled, headers, body_template, created_at, last_status, last_error, last_event,"
    " last_sent_at"
)


@dataclasses.dataclass(frozen=True, slots=True)
class Config:
    """What the user sets. ``secret`` and the header values are write-only for the API."""

    url: str
    events: tuple[str, ...]
    enabled: bool = True
    secret: str | None = None
    headers: tuple[tuple[str, str], ...] = ()
    body_template: str | None = None

    @property
    def json_body(self) -> bool:
        """The body is JSON unless an own ``Content-Type`` says otherwise."""
        for name, value in self.headers:
            if name.lower() == "content-type":
                return "json" in value.lower()
        return True


@dataclasses.dataclass(frozen=True, slots=True)
class Webhook:
    id: int
    config: Config
    created_at: int
    last_status: int | None = None  # HTTP status of the last attempt, None if the request did not get through
    last_error: str | None = None
    last_event: str | None = None
    last_sent_at: int | None = None


def _webhook(row: tuple[Any, ...]) -> Webhook:
    wid, url, events, secret, enabled, headers, template, created, status, error, event, sent = row
    config = Config(
        url=url,
        events=tuple(json.loads(events)),
        enabled=bool(enabled),
        secret=secret,
        headers=tuple((name, value) for name, value in json.loads(headers)),
        body_template=template,
    )
    return Webhook(wid, config, created, status, error, event, sent)


def _row(config: Config) -> tuple[Any, ...]:
    return (
        config.url,
        json.dumps(list(config.events)),
        config.secret or None,
        int(config.enabled),
        json.dumps([list(h) for h in config.headers]),
        config.body_template or None,
    )


class WebhookStore:
    def __init__(self, state: StateStore) -> None:
        self._db = state.db

    def add(self, config: Config) -> Webhook:
        _, wid = self._db.run(
            "INSERT INTO webhook (url, events, secret, enabled, headers, body_template, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (*_row(config), int(time.time())),
        )
        found = self.get(int(wid or 0))
        assert found is not None
        return found

    def update(self, webhook_id: int, config: Config) -> Webhook | None:
        changed, _ = self._db.run(
            "UPDATE webhook SET url = ?, events = ?, secret = ?, enabled = ?, headers = ?, body_template = ?"
            " WHERE id = ?",
            (*_row(config), webhook_id),
        )
        return self.get(webhook_id) if changed else None

    def get(self, webhook_id: int) -> Webhook | None:
        row = self._db.one(f"SELECT {_COLUMNS} FROM webhook WHERE id = ?", (webhook_id,))
        return None if row is None else _webhook(row)

    def entries(self) -> list[Webhook]:
        return [_webhook(r) for r in self._db.all(f"SELECT {_COLUMNS} FROM webhook ORDER BY id")]

    def delete(self, webhook_id: int) -> bool:
        changed, _ = self._db.run("DELETE FROM webhook WHERE id = ?", (webhook_id,))
        return changed > 0

    def record(self, webhook_id: int, event: str, status: int | None, error: str | None) -> None:
        self._db.run(
            "UPDATE webhook SET last_status = ?, last_error = ?, last_event = ?, last_sent_at = ? WHERE id = ?",
            (status, error, event, int(time.time()), webhook_id),
        )


# -- bodies ------------------------------------------------------------------------------------------------------------


def _jsonable(value: Any) -> Any:
    if isinstance(value, dt.datetime):
        return value.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")
    return value


def payload(event: Event) -> dict[str, Any]:
    data = {f.name: _jsonable(getattr(event, f.name)) for f in dataclasses.fields(event) if f.name != "at"}
    return {"event": event.name, "time": _jsonable(event.at), "data": data}


def sample_payload(webhook_id: int, download_dir: Path = Path("/downloads")) -> dict[str, Any]:
    """The test event: the fields of a finished download with harmless example values, so a template sees what it
    will see later (a Sonarr scan gets a path that does not exist instead of the whole download folder)."""
    path = "mtdl-web-test/mtdl-web - Test.mp4"
    data = {
        "webhook_id": webhook_id,
        "download_id": 0,
        "film_id": "0",
        "channel": "ARD",
        "topic": "mtdl-web",
        "title": "Test",
        "path": path,
        "subscription_id": None,
        "file": str(download_dir / path),
        "library_id": None,
        "size": 0,
        "subtitle_path": None,
        "subtitle_file": None,
    }
    return {"event": TEST_EVENT, "time": _jsonable(dt.datetime.now(dt.UTC)), "data": data}


def _fill(template: str, value: Callable[[re.Match[str], bool], str], json_body: bool) -> str:
    """Replace every placeholder by ``value(match, inside_a_json_string)``."""
    out: list[str] = []
    pos = 0
    inside = False
    for m in PLACEHOLDER.finditer(template):
        text = template[pos : m.start()]
        if json_body:
            escaped = False
            for char in text:
                if escaped:
                    escaped = False
                elif char == "\\" and inside:
                    escaped = True
                elif char == '"':
                    inside = not inside
        out += (text, value(m, inside))
        pos = m.end()
    out.append(template[pos:])
    return "".join(out)


def render(template: str, body: Mapping[str, Any], *, json_body: bool = True) -> str:
    def value(m: re.Match[str], inside: bool) -> str:
        found: Any = body
        for part in m[1].split("."):
            found = found.get(part) if isinstance(found, Mapping) else None
        if not json_body:
            return "" if found is None else json.dumps(found) if isinstance(found, bool) else str(found)
        if not inside:
            return json.dumps(found, ensure_ascii=False)
        if found is None:
            return ""
        text = found if isinstance(found, str) else json.dumps(found)
        return json.dumps(text, ensure_ascii=False)[1:-1]

    return _fill(template, value, json_body)


def problem(config: Config) -> dict[str, Any] | None:
    """What is wrong with a configuration, as the API's 422 detail; None if nothing."""
    names = [name.lower() for name, _ in config.headers]
    bad = [
        name
        for name, value in config.headers
        if not HEADER_NAME.fullmatch(name)
        or not HEADER_VALUE.fullmatch(value)
        or name.lower() in RESERVED_HEADERS
        or names.count(name.lower()) > 1
    ]
    if bad or len(config.headers) > MAX_HEADERS:
        return {"code": "invalid_headers", "headers": bad}
    template = config.body_template
    if not template:
        return None
    unknown = sorted({field for field in PLACEHOLDER.findall(template) if field not in TEMPLATE_FIELDS})
    if unknown:
        return {"code": "unknown_placeholders", "placeholders": unknown}
    if config.json_body:
        # Placeholders replaced by text of their own length: the error position points into the template.
        def stand_in(m: re.Match[str], inside: bool) -> str:
            return "x" * len(m[0]) if inside else "null".ljust(len(m[0]))

        try:
            json.loads(_fill(template, stand_in, json_body=True))
        except json.JSONDecodeError as exc:
            return {"code": "invalid_json", "line": exc.lineno, "column": exc.colno, "message": exc.msg}
    return None


def redact(text: str, config: Config) -> str:
    """Error texts reach the API and the log: never with the secret or a header value in them."""
    for value in (config.secret or "", *(value for _, value in config.headers)):
        if len(value) >= 4:
            text = text.replace(value, "***")
    return text


def signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# -- delivery ----------------------------------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class Delivery:
    status: int | None
    error: str | None

    @property
    def ok(self) -> bool:
        return self.status is not None and 200 <= self.status < 300


class WebhookSender:
    def __init__(
        self,
        store: WebhookStore,
        client: httpx.AsyncClient,
        bus: EventBus,
        retry_delays: Sequence[float] = (5, 30),
        *,
        download_dir: Path = Path("/downloads"),
    ) -> None:
        self.store = store
        self._client = client
        self._download_dir = download_dir
        self._delays = tuple(retry_delays)
        self._tasks: set[asyncio.Task[None]] = set()
        bus.subscribe(self._on_event)

    def _on_event(self, event: Event) -> None:
        body = payload(event)
        for hook in self.store.entries():
            if hook.config.enabled and event.name in hook.config.events:
                task = asyncio.create_task(self._deliver_with_retries(hook, body), name=f"webhook-{hook.id}")
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

    async def send(self, hook: Webhook, body: dict[str, Any]) -> Delivery:
        config = hook.config
        try:
            if config.body_template:
                raw = render(config.body_template, body, json_body=config.json_body).encode()
            else:
                raw = json.dumps(body, ensure_ascii=False).encode()
            headers = httpx.Headers({"Content-Type": "application/json", "X-Mtdl-Event": str(body["event"])})
            for name, value in config.headers:
                headers[name] = value  # own headers win, Content-Type included
            if config.secret:
                headers["X-Mtdl-Signature"] = signature(config.secret, raw)
            # Redirects are not followed: the POST would become a GET, own headers would go to the new host.
            response = await self._client.post(
                config.url, content=raw, headers=headers, timeout=TIMEOUT, follow_redirects=False
            )
        except (httpx.HTTPError, ValueError) as exc:  # network, protocol, a header value the client refuses
            return Delivery(None, redact(f"{type(exc).__name__}: {exc}", config))
        status = response.status_code
        if 200 <= status < 300:
            return Delivery(status, None)
        if 300 <= status < 400:
            return Delivery(status, f"HTTP {status}, redirect to {response.headers.get('location', '?')}")
        return Delivery(status, f"HTTP {status}")

    async def _deliver_with_retries(self, hook: Webhook, body: dict[str, Any]) -> None:
        delivery = await self.send(hook, body)
        for delay in self._delays:
            if delivery.ok or (delivery.status is not None and 300 <= delivery.status < 500 and delivery.status != 429):
                break  # done, or the receiver rejects or redirects the request: trying again will not help
            await asyncio.sleep(delay)
            delivery = await self.send(hook, body)
        self.store.record(hook.id, str(body["event"]), delivery.status, delivery.error)
        if not delivery.ok:
            host = urlsplit(hook.config.url).hostname  # the full url often carries a token (Discord, Gotify)
            log.warning("webhook %d (%s) failed for %s: %s", hook.id, host, body["event"], delivery.error)

    async def test(self, hook: Webhook) -> Delivery:
        """One attempt with a test event, answered directly (the settings show the result)."""
        delivery = await self.send(hook, sample_payload(hook.id, self._download_dir))
        self.store.record(hook.id, TEST_EVENT, delivery.status, delivery.error)
        return delivery

    async def drain(self) -> None:
        """Wait for the deliveries under way (tests, orderly shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        with contextlib.suppress(Exception):
            await asyncio.gather(*self._tasks, return_exceptions=True)
