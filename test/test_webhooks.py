import dataclasses
import datetime as dt
import hashlib
import hmac
import json
import sqlite3
from pathlib import Path

import httpx
import pytest
from test_downloads import MediaServer, running_app

from mtdl_web.db import migrate
from mtdl_web.events import DownloadCompleted, DownloadFailed, EventBus, FilmlistUpdated
from mtdl_web.http import make_client
from mtdl_web.state import MIGRATIONS, StateStore
from mtdl_web.webhooks import (
    EVENT_TYPES,
    EVENTS,
    TEMPLATE_FIELDS,
    Config,
    WebhookSender,
    WebhookStore,
    payload,
    problem,
    redact,
    render,
)


class Receiver:
    """The other side: records requests, answers with the next status of a script."""

    def __init__(self, *statuses: int) -> None:
        self.statuses = list(statuses)
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status = self.statuses.pop(0) if self.statuses else 200
        if status == 0:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(status)


COMPLETED = DownloadCompleted(
    download_id=7,
    film_id="9113043070428711758",
    channel="MDR",
    topic="Das Gipfeltreffen",
    title="Das Beste kommt noch! (58)",
    path="Das Gipfeltreffen/Season 01/Das Gipfeltreffen - S01E58 - Das Beste kommt noch!.mp4",
    subscription_id=1,
    file="/downloads/Das Gipfeltreffen/Season 01/Das Gipfeltreffen - S01E58 - Das Beste kommt noch!.mp4",
    size=267386880,
    subtitle_path=None,
    at=dt.datetime(2026, 10, 13, 2, 0, tzinfo=dt.UTC),
)
SONARR = """{
  "name": "DownloadedEpisodesScan",
  "path": "/downloads/{{data.path}}",
  "importMode": "Move",
  "size": {{ data.size }},
  "subscription": {{data.subscription_id}},
  "subtitles": {{data.subtitle_path}},
  "note": "\\"{{data.title}}\\" ({{data.channel}}, {{event}})"
}"""


async def deliver(receiver: Receiver, tmp_path: Path, *hooks: Config) -> WebhookStore:
    state = StateStore(tmp_path / "state.sqlite")
    store = WebhookStore(state)
    for config in hooks:
        store.add(config)
    bus = EventBus()
    async with httpx.AsyncClient(transport=httpx.MockTransport(receiver.handler)) as client:
        sender = WebhookSender(store, client, bus, retry_delays=(0, 0))
        await bus.publish(COMPLETED)
        await bus.publish(
            FilmlistUpdated(kind="diff", list_id="x", created=COMPLETED.at, added=3, changed=1, removed=0, total=9)
        )
        await sender.drain()
    return store


@pytest.mark.anyio
async def test_events_arrive_signed(tmp_path: Path) -> None:
    receiver = Receiver()
    store = await deliver(
        receiver, tmp_path, Config("http://ha.example/hook", ("download.completed",), secret="geheim")
    )
    (request,) = receiver.requests
    body = request.content
    assert json.loads(body) == {
        "event": "download.completed",
        "time": "2026-10-13T02:00:00Z",
        "data": {
            "download_id": 7,
            "film_id": "9113043070428711758",
            "channel": "MDR",
            "topic": "Das Gipfeltreffen",
            "title": "Das Beste kommt noch! (58)",
            "path": COMPLETED.path,
            "subscription_id": 1,
            "file": COMPLETED.file,
            "library_id": None,
            "size": 267386880,
            "subtitle_path": None,
            "subtitle_file": None,
        },
    }
    expected = "sha256=" + hmac.new(b"geheim", body, hashlib.sha256).hexdigest()
    assert request.headers["x-mtdl-signature"] == expected
    assert request.headers["x-mtdl-event"] == "download.completed"
    (hook,) = store.entries()
    assert (hook.last_status, hook.last_error, hook.last_event) == (200, None, "download.completed")


@pytest.mark.anyio
async def test_only_chosen_events_and_no_signature_without_secret(tmp_path: Path) -> None:
    receiver = Receiver()
    await deliver(receiver, tmp_path, Config("http://a.example/", ("filmlist.updated",)))
    (request,) = receiver.requests
    assert json.loads(request.content)["data"]["created"] == "2026-10-13T02:00:00Z"
    assert "x-mtdl-signature" not in request.headers


@pytest.mark.anyio
async def test_temporary_failures_are_retried(tmp_path: Path) -> None:
    receiver = Receiver(503, 0, 200)
    store = await deliver(receiver, tmp_path, Config("http://a.example/", ("download.completed",)))
    assert len(receiver.requests) == 3
    assert store.entries()[0].last_status == 200


@pytest.mark.anyio
async def test_rejections_and_dead_receivers(tmp_path: Path) -> None:
    receiver = Receiver(404, 0, 0, 0)
    store = await deliver(
        receiver,
        tmp_path,
        Config("http://gone.example/", ("download.completed",)),
        Config("http://down.example/", ("download.completed",)),
    )
    hosts = [r.url.host for r in receiver.requests]
    assert hosts.count("gone.example") == 1  # 404: the receiver says no, trying again will not help
    assert hosts.count("down.example") == 3
    gone, down = store.entries()
    assert (gone.last_status, gone.last_error) == (404, "HTTP 404")
    assert down.last_status is None and (down.last_error or "").startswith("ConnectError")


@pytest.mark.anyio
async def test_disabled_hooks_stay_quiet(tmp_path: Path) -> None:
    receiver = Receiver()
    state = StateStore(tmp_path / "state.sqlite")
    store = WebhookStore(state)
    store.add(Config("http://a.example/", ("download.failed",), enabled=False))
    bus = EventBus()
    async with httpx.AsyncClient(transport=httpx.MockTransport(receiver.handler)) as client:
        sender = WebhookSender(store, client, bus)
        failed = DownloadFailed(
            **{
                k: getattr(COMPLETED, k)
                for k in ("download_id", "film_id", "channel", "topic", "title", "path", "subscription_id")
            },
            error="HTTP 404",
            will_retry=False,
        )
        await bus.publish(failed)
        await sender.drain()
    assert receiver.requests == []
    state.close()


@pytest.mark.anyio
async def test_template_and_own_headers_for_sonarr(tmp_path: Path) -> None:
    receiver = Receiver()
    sonarr = Config(
        "http://sonarr.example/api/v3/command",
        ("download.completed",),
        secret="geheim",
        headers=(("X-Api-Key", "0123abcd"),),
        body_template=SONARR,
    )
    await deliver(receiver, tmp_path, sonarr)
    (request,) = receiver.requests
    assert json.loads(request.content) == {
        "name": "DownloadedEpisodesScan",
        "path": "/downloads/" + COMPLETED.path,
        "importMode": "Move",
        "size": 267386880,
        "subscription": 1,
        "subtitles": None,
        "note": '"Das Beste kommt noch! (58)" (MDR, download.completed)',
    }
    assert request.headers["x-api-key"] == "0123abcd"
    assert request.headers["content-type"] == "application/json"
    expected = "sha256=" + hmac.new(b"geheim", request.content, hashlib.sha256).hexdigest()
    assert request.headers["x-mtdl-signature"] == expected  # over the body actually sent


@pytest.mark.anyio
async def test_plain_text_for_ntfy(tmp_path: Path) -> None:
    receiver = Receiver()
    ntfy = Config(
        "http://ntfy.example/mtdl",
        ("download.completed",),
        headers=(("content-type", "text/plain; charset=utf-8"), ("Title", "mtdl-web")),
        body_template="{{data.title}} ist fertig ({{data.channel}}){{data.subtitle_path}}",
    )
    await deliver(receiver, tmp_path, ntfy)
    (request,) = receiver.requests
    assert request.content.decode() == "Das Beste kommt noch! (58) ist fertig (MDR)"
    assert request.headers.get_list("content-type") == ["text/plain; charset=utf-8"]  # replaced, not added
    assert request.headers["title"] == "mtdl-web"


def test_render_inside_and_outside_json_strings() -> None:
    body = payload(dataclasses.replace(COMPLETED, title='Er sagt "Hallo" \\ Tschüss\n', subscription_id=None))
    rendered = render(
        '{"t": "{{data.title}}", "raw": {{data.title}}, "s": "{{data.size}}", "n": {{data.subscription_id}},'
        ' "e": "{{data.subscription_id}}", "q": "\\" {{event}}", "x": "{{data.kind}}"}',
        body,
    )
    assert json.loads(rendered) == {
        "t": 'Er sagt "Hallo" \\ Tschüss\n',
        "raw": 'Er sagt "Hallo" \\ Tschüss\n',
        "s": "267386880",
        "n": None,
        "e": "",
        "q": '" download.completed',
        "x": "",  # a field of another event
    }
    text = render("{{data.title}}|{{data.size}}|{{data.subscription_id}}", body, json_body=False)
    assert text == 'Er sagt "Hallo" \\ Tschüss\n|267386880|'


def test_problems_are_found_when_saving() -> None:
    def check(template: str | None = None, *headers: tuple[str, str]) -> object:
        return problem(Config("http://a.example/", ("download.completed",), headers=headers, body_template=template))

    assert check(SONARR, ("X-Api-Key", "abc")) is None
    assert check('{"a": "{{data.nope}}", "b": {{secret}}}') == {
        "code": "unknown_placeholders",
        "placeholders": ["data.nope", "secret"],
    }
    broken = check('{\n  "path": "{{data.path}}"\n  "size": {{data.size}}\n}')
    assert broken == {"code": "invalid_json", "line": 3, "column": 3, "message": "Expecting ',' delimiter"}
    assert check("{{data.title}} ist fertig", ("Content-Type", "text/plain")) is None
    assert check("{{data.title}} ist fertig") == {  # plain text needs Content-Type text/plain
        "code": "invalid_json",
        "line": 1,
        "column": 16,
        "message": "Extra data",
    }
    assert check(None, ("X-Key", "a\r\nX-Evil: 1")) == {"code": "invalid_headers", "headers": ["X-Key"]}
    assert check(None, ("Bad Name", "x"), ("Content-Length", "3"), ("X-Mtdl-Signature", "x")) == {
        "code": "invalid_headers",
        "headers": ["Bad Name", "Content-Length", "X-Mtdl-Signature"],
    }
    assert check(None, ("X-Key", "a"), ("x-key", "b")) == {"code": "invalid_headers", "headers": ["X-Key", "x-key"]}
    assert check(None, ("X-Key", "Grüße")) == {"code": "invalid_headers", "headers": ["X-Key"]}


def test_template_fields_cover_every_event() -> None:
    assert tuple(cls.name for cls in EVENT_TYPES) == EVENTS
    assert {"data.path", "data.size", "data.will_retry", "data.total", "data.webhook_id"} <= TEMPLATE_FIELDS
    assert "data.at" not in TEMPLATE_FIELDS


def test_migration_adds_headers_and_template(tmp_path: Path) -> None:
    con = sqlite3.connect(tmp_path / "state.sqlite")
    migrate(con, MIGRATIONS[:6])
    con.execute(
        "INSERT INTO webhook (url, events, secret, enabled, created_at) VALUES (?, ?, ?, 1, 0)",
        ("http://a.example/", '["download.completed"]', "geheim"),
    )
    con.commit()
    con.close()
    state = StateStore(tmp_path / "state.sqlite")
    (hook,) = WebhookStore(state).entries()
    assert hook.config == Config("http://a.example/", ("download.completed",), secret="geheim")
    state.close()


def test_manage_and_test_through_the_api(tmp_path: Path) -> None:
    with running_app(tmp_path, MediaServer()) as api:
        created = api.post(
            "/api/v1/webhooks",
            json={"url": "http://ha.example/api/webhook/mtdl", "events": ["download.completed"], "secret": "geheim"},
        )
        assert created.status_code == 201
        hook = created.json()
        assert hook["has_secret"] is True and "secret" not in hook
        assert (hook["header_names"], hook["body_template"]) == ([], None)
        assert (
            api.post("/api/v1/webhooks", json={"url": "ftp://x", "events": ["download.completed"]}).status_code == 422
        )
        assert api.post("/api/v1/webhooks", json={"url": "http://x", "events": ["nonsense"]}).status_code == 422
        assert api.post("/api/v1/webhooks", json={"url": "http://x", "events": []}).status_code == 422
        kept = api.put(f"/api/v1/webhooks/{hook['id']}", json={"url": hook["url"], "events": ["download.failed"]})
        assert kept.json()["has_secret"] is True and kept.json()["events"] == ["download.failed"]
        removed = api.put(
            f"/api/v1/webhooks/{hook['id']}", json={"url": hook["url"], "events": ["download.failed"], "secret": ""}
        )
        assert removed.json()["has_secret"] is False
        result = api.post(f"/api/v1/webhooks/{hook['id']}/test").json()
        assert result == {"ok": True, "status": 200, "error": None}
        (listed,) = api.get("/api/v1/webhooks").json()
        assert (listed["last_event"], listed["last_status"]) == ("webhook.test", 200)
        assert api.delete(f"/api/v1/webhooks/{hook['id']}").status_code == 204
        assert api.post(f"/api/v1/webhooks/{hook['id']}/test").status_code == 404
        gone = api.put(f"/api/v1/webhooks/{hook['id']}", json={"url": "http://x", "events": ["download.failed"]})
        assert gone.status_code == 404


def test_templates_and_headers_through_the_api(tmp_path: Path) -> None:
    media = MediaServer()
    with running_app(tmp_path, media) as api:
        sonarr = {
            "url": "http://sonarr.example/api/v3/command",
            "events": ["download.completed"],
            "headers": {"X-Api-Key": "0123abcd"},
            "body_template": SONARR,
        }
        created = api.post("/api/v1/webhooks", json=sonarr)
        assert created.status_code == 201
        hook = created.json()
        assert (hook["header_names"], hook["body_template"]) == (["X-Api-Key"], SONARR)
        assert "0123abcd" not in created.text and "0123abcd" not in api.get("/api/v1/webhooks").text

        def sent() -> httpx.Request:
            assert api.post(f"/api/v1/webhooks/{hook['id']}/test").json()["ok"] is True
            return media.requests[-1]

        request = sent()
        assert request.headers["x-api-key"] == "0123abcd"
        body = json.loads(request.content)
        assert body["path"] == "/downloads/mtdl-web-test/mtdl-web - Test.mp4"  # harmless: does not exist
        assert body["note"] == '"Test" (ARD, webhook.test)'

        url = f"/api/v1/webhooks/{hook['id']}"
        kept = api.put(url, json={**sonarr, "headers": None, "body_template": SONARR.replace("Move", "Copy")})
        assert kept.json()["header_names"] == ["X-Api-Key"]
        request = sent()
        assert request.headers["x-api-key"] == "0123abcd" and json.loads(request.content)["importMode"] == "Copy"

        one_more = api.put(url, json={**sonarr, "headers": {"x-api-key": None, "X-Source": "mtdl"}}).json()
        assert one_more["header_names"] == ["x-api-key", "X-Source"]
        request = sent()
        assert (request.headers["x-api-key"], request.headers["x-source"]) == ("0123abcd", "mtdl")
        missing = api.put(url, json={**sonarr, "headers": {"X-Other": None}})
        assert missing.json()["detail"] == {"code": "invalid_headers", "headers": ["X-Other"]}

        cleared = api.put(url, json={**sonarr, "headers": {}, "body_template": ""}).json()
        assert (cleared["header_names"], cleared["body_template"]) == ([], None)
        request = sent()
        assert "x-api-key" not in request.headers and json.loads(request.content)["event"] == "webhook.test"

        unknown = api.put(url, json={**sonarr, "body_template": '{"a": "{{data.nope}}"}'})
        assert unknown.status_code == 422
        assert unknown.json()["detail"] == {"code": "unknown_placeholders", "placeholders": ["data.nope"]}
        broken = api.post("/api/v1/webhooks", json={**sonarr, "body_template": '{"a": 1,}'})
        assert (broken.status_code, broken.json()["detail"]["code"]) == (422, "invalid_json")
        injected = api.post("/api/v1/webhooks", json={**sonarr, "headers": {"X-Api-Key": "a\nb"}})
        assert injected.json()["detail"] == {"code": "invalid_headers", "headers": ["X-Api-Key"]}
        text = {**sonarr, "headers": {"Content-Type": "text/plain"}, "body_template": "{{data.title}} ist da"}
        assert api.post("/api/v1/webhooks", json=text).status_code == 201
        assert len(api.get("/api/v1/webhooks").json()) == 2


def test_header_values_without_whitespace_at_the_ends_and_never_in_error_texts() -> None:
    def check(*headers: tuple[str, str]) -> object:
        return problem(Config("http://a.example/", ("download.completed",), headers=headers))

    assert check(("X-Key", "abc\n")) == {"code": "invalid_headers", "headers": ["X-Key"]}  # "$" allowed a final "\n"
    assert check(("X-Key\n", "abc")) == {"code": "invalid_headers", "headers": ["X-Key\n"]}
    assert check(("X-Key", " abc")) == check(("X-Key", "abc\t")) == {"code": "invalid_headers", "headers": ["X-Key"]}
    assert check(("X-Key", "a b\tc"), ("X-Empty", "")) is None
    config = Config(
        "http://a.example/", ("download.completed",), secret="geheim-123", headers=(("X-Api-Key", "0123abcd"),)
    )
    text = "LocalProtocolError: Illegal header value b'0123abcd ' (geheim-123)"
    assert redact(text, config) == "LocalProtocolError: Illegal header value b'*** ' (***)"


@pytest.mark.anyio
async def test_redirects_are_reported_not_followed(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def moved(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(301, headers={"location": "https://ha.example/hook"})

    state = StateStore(tmp_path / "state.sqlite")
    store = WebhookStore(state)
    store.add(Config("http://ha.example/hook", ("download.completed",), headers=(("X-Api-Key", "0123abcd"),)))
    bus = EventBus()
    async with make_client(httpx.MockTransport(moved)) as client:  # the app's client follows redirects elsewhere
        sender = WebhookSender(store, client, bus, retry_delays=(0, 0))
        await bus.publish(COMPLETED)
        await sender.drain()
    assert len(requests) == 1  # neither followed (a GET without body, the key to another host) nor retried
    (hook,) = store.entries()
    assert (hook.last_status, hook.last_error) == (301, "HTTP 301, redirect to https://ha.example/hook")
    state.close()


def test_pasted_keys_are_trimmed(tmp_path: Path) -> None:
    media = MediaServer()
    with running_app(tmp_path, media) as api:
        body = {"url": "http://a.example/", "events": ["download.completed"], "headers": {"X-Api-Key": "0123abcd \t"}}
        hook = api.post("/api/v1/webhooks", json=body).json()
        assert api.post(f"/api/v1/webhooks/{hook['id']}/test").json()["ok"] is True
        assert media.requests[-1].headers["x-api-key"] == "0123abcd"
