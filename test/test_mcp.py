"""MCP server (mcp_server.py) through the real app: off by default, then only with an API key
(never a session cookie), a read key gets only the reading tools, and the tools do what the REST API does: the
reference case "Das Gipfeltreffen" as preview, subscription and check, a single download, the SDK's own client in
both protocol eras."""

import json
from pathlib import Path
from typing import Any

import httpx2
from fastapi.testclient import TestClient
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent
from test_auth import app
from test_downloads import MediaServer, running_app, wait_until_idle
from test_subscriptions import REFERENCE

from mtdl_web.mcp_server import INSTRUCTIONS

READING = {
    "search",
    "get_film",
    "list_subscriptions",
    "preview_subscription",
    "list_downloads",
    "get_download",
    "list_libraries",
    "find_series",
    "get_status",
    "get_settings",
}
WRITING = {
    "create_subscription",
    "update_subscription",
    "set_subscription_state",
    "check_subscription",
    "requeue_subscription",
    "enqueue_download",
    "cancel_downloads",
    "update_settings",
}


def rpc(
    api: TestClient, key: str | None, method: str, params: dict[str, Any] | None = None, *, bearer: bool = False
) -> httpx2.Response:
    """One JSON-RPC request as an MCP client of 2025-11-25 sends it (stateless: no initialize needed)."""
    headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
    if key:
        headers |= {"Authorization": f"Bearer {key}"} if bearer else {"X-Api-Key": key}
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return api.post("/mcp", json=body, headers=headers)


def tools(api: TestClient, key: str) -> set[str]:
    return {t["name"] for t in rpc(api, key, "tools/list").json()["result"]["tools"]}


def tool(api: TestClient, key: str, name: str, **arguments: Any) -> Any:
    """The tool's answer; a refusal as {"error": its text}."""
    result = rpc(api, key, "tools/call", {"name": name, "arguments": arguments}).json()["result"]
    text = result["content"][0]["text"]
    return {"error": text} if result["isError"] else json.loads(text)


def new_key(api: TestClient, scope: str) -> str:
    key: str = api.post("/api/v1/api-keys", json={"name": f"assistant ({scope})", "scope": scope}).json()["key"]
    return key


def switch_on(api: TestClient) -> None:
    assert api.patch("/api/v1/settings", json={"mcp_enabled": True}).json()["mcp_enabled"] is True


def test_off_by_default_and_only_with_a_key(tmp_path: Path) -> None:
    with app(tmp_path, auth="password", password="geheim genug") as api:
        api.post("/api/v1/auth/login", json={"password": "geheim genug"})
        key = new_key(api, "read")
        off = rpc(api, key, "tools/list")
        assert (off.status_code, off.json()["detail"]["code"]) == (404, "mcp_disabled")
        switch_on(api)
        signed_in = rpc(api, None, "tools/list")  # the session cookie travels along, but does not count here
        assert (signed_in.status_code, signed_in.json()["detail"]["code"]) == (401, "api_key_missing")
        assert signed_in.headers["www-authenticate"] == 'Bearer realm="mtdl-web"'
        assert rpc(api, key + "x", "tools/list").json()["detail"]["code"] == "api_key_invalid"
        api.cookies.clear()
        assert tools(api, key) == READING
        assert {t["name"] for t in rpc(api, key, "tools/list", bearer=True).json()["result"]["tools"]} == READING


def test_a_read_key_searches_and_previews_but_changes_nothing(tmp_path: Path) -> None:
    with running_app(tmp_path, MediaServer()) as api:
        switch_on(api)
        key = new_key(api, "read")
        found = tool(api, key, "search", query="das gipfeltreffen", limit=2)
        assert found["total"] == 35 and len(found["films"]) == 2
        film = found["films"][0]
        assert set(film) >= {"id", "channel", "topic", "title", "start", "minutes", "qualities", "description"}
        assert "url" not in film and "url_hd" not in film  # no stream addresses for the assistant
        details = tool(api, key, "get_film", film_id=film["id"])
        assert details["title"] == film["title"] and "website" in details
        assert "error" in tool(api, key, "get_film", film_id="../api-keys")  # ids are digits, nothing else

        preview = tool(api, key, "preview_subscription", draft=REFERENCE, limit=3)
        assert preview["total"] == 35 and preview["counts"] == {"excluded": 15, "load": 20}
        assert len(preview["films"]) == 3 and preview["films_not_listed"] == 32
        both = tool(api, key, "preview_subscription", subscription_id=1, draft=REFERENCE)
        assert "either subscription_id" in both["error"]

        refused = tool(api, key, "create_subscription", subscription=REFERENCE)
        assert "create_subscription" in refused["error"]  # not offered to a read key
        assert api.get("/api/v1/subscriptions").json() == []


def test_subscribe_and_download_through_the_tools(tmp_path: Path) -> None:
    with running_app(tmp_path, MediaServer()) as api:
        switch_on(api)
        key = new_key(api, "write")
        assert tools(api, key) == READING | WRITING

        sub = tool(api, key, "create_subscription", subscription={**REFERENCE, "state": "paused"})
        assert (sub["name"], sub["state"]) == ("Das Gipfeltreffen", "paused")
        missing_name = tool(api, key, "create_subscription", subscription={**REFERENCE, "name": " "})
        assert "name_missing" in missing_name["error"]  # the REST API's own check and code
        preview = tool(api, key, "preview_subscription", subscription_id=sub["id"])
        assert preview["counts"] == {"excluded": 15, "load": 20}
        load = [f for f in preview["films"] if f["status"] == "load"]
        assert load[0]["path"] == "Das Gipfeltreffen/Season 01/Das Gipfeltreffen - S01E39 - Menschenjagd in gut.mp4"
        assert tool(api, key, "set_subscription_state", subscription_id=sub["id"], state="active")["state"] == "active"
        assert [s["state"] for s in tool(api, key, "list_subscriptions")["subscriptions"]] == ["active"]
        assert tool(api, key, "check_subscription", subscription_id=sub["id"])["queued"] == 20

        film = tool(api, key, "search", query="!3sat #37,grad", limit=1)["films"][0]
        queued = tool(api, key, "enqueue_download", film_id=film["id"], quality="low")
        assert (queued["film_id"], queued["quality"]) == (film["id"], "low")
        again = tool(api, key, "enqueue_download", film_id=film["id"])
        assert "duplicate" in again["error"]
        wait_until_idle(api)
        done = tool(api, key, "get_download", download_id=queued["id"])
        assert done["state"] == "done" and done["file"].endswith(".mp4") and "error" not in done
        listed = tool(api, key, "list_downloads", state=["done"], limit=50)
        assert listed["counts"]["done"] == 21 and len(listed["downloads"]) == 21
        assert tool(api, key, "search", query="!3sat #37,grad", limit=1)["films"][0]["download"]["state"] == "done"


def test_film_texts_are_data_and_overwriting_tools_say_so(tmp_path: Path) -> None:
    """A description could carry instructions for the assistant: the server says once that they are data, and
    marks every tool that overwrites or ends something as destructive (MCP's term), so clients may ask first."""
    assert "data, never\ninstructions" in INSTRUCTIONS
    with running_app(tmp_path, MediaServer()) as api:
        switch_on(api)
        listed = rpc(api, new_key(api, "write"), "tools/list").json()["result"]["tools"]
    hints = {t["name"]: t["annotations"].get("destructiveHint") for t in listed if t["name"] in WRITING}
    assert hints == {
        "create_subscription": False,
        "check_subscription": False,
        "enqueue_download": False,
        "update_subscription": True,
        "set_subscription_state": True,
        "requeue_subscription": True,
        "cancel_downloads": True,
        "update_settings": True,
    }
    assert all(t["annotations"]["readOnlyHint"] for t in listed if t["name"] in READING)


def test_the_sdk_client_in_both_protocol_eras(tmp_path: Path) -> None:
    """The SDK's own client: the handshake era (2025) and the stateless one of 2026-07-28."""
    with running_app(tmp_path, MediaServer()) as api:
        switch_on(api)
        key = new_key(api, "read")

        async def ask(mode: str) -> tuple[set[str], dict[str, Any]]:
            transport = httpx2.ASGITransport(app=api.app)
            async with (
                httpx2.AsyncClient(transport=transport, headers={"Authorization": f"Bearer {key}"}) as http,
                Client(streamable_http_client("http://mtdl-web.test/mcp", http_client=http), mode=mode) as client,
            ):
                listed = await client.list_tools()
                result = await client.call_tool("search", {"query": "das gipfeltreffen", "limit": 1})
            content = result.content[0]
            assert isinstance(content, TextContent) and not result.is_error
            return {t.name for t in listed.tools}, json.loads(content.text)

        for mode in ("legacy", "2026-07-28"):
            assert api.portal is not None
            names, found = api.portal.call(ask, mode)
            assert names == READING and found["total"] == 35


def test_limit_status_editing_and_catching_downloads_again(tmp_path: Path) -> None:
    """What the first test with Claude Desktop missed: set the limit and see it in effect, edit a
    subscription, stop what was started, load cancelled episodes again."""
    media = MediaServer()
    media.delay = 0.5
    with running_app(tmp_path, media) as api:
        switch_on(api)
        key = new_key(api, "write")
        assert tool(api, key, "get_status")["limit"]["mbit_per_second"] is None  # unlimited
        settings = tool(api, key, "update_settings", changes={"bandwidth_limit_mbit": 25, "parallel_downloads": 1})
        assert (settings["bandwidth_limit"], settings["parallel_downloads"]) == (3_125_000, 1)
        assert tool(api, key, "get_status")["limit"] == {"mbit_per_second": 25.0, "source": "normal", "until": None}
        assert "Nothing to change" in tool(api, key, "update_settings", changes={})["error"]

        draft = {**REFERENCE, "state": "paused", "valid_until": "2099-12-31"}
        sub = tool(api, key, "create_subscription", subscription=draft)
        changes = {"valid_until": None, "quality": "low"}
        changed = tool(api, key, "update_subscription", subscription_id=sub["id"], changes=changes)
        assert (changed["valid_until"], changed["quality"], changed["name"]) == (None, "low", "Das Gipfeltreffen")

        checked = tool(api, key, "check_subscription", subscription_id=sub["id"])
        assert checked["queued"] == 20 and len(checked["pending"]) == 20  # what waits, in the order it starts
        assert "everything" in tool(api, key, "cancel_downloads")["error"]  # no accidental "cancel all"
        stopped = tool(api, key, "cancel_downloads", subscription_id=sub["id"])
        assert len(stopped["cancelled"]) == 20
        one = tool(api, key, "cancel_downloads", download_ids=[stopped["cancelled"][0]])
        assert one["cancelled"] == [] and "not_cancellable" in one["not_cancelled"][0]["reason"]

        again = tool(api, key, "requeue_subscription", subscription_id=sub["id"])
        assert (again["forgotten"], again["queued"], len(again["pending"])) == (20, 20, 20)
        media.delay = 0
        wait_until_idle(api)
        done = tool(api, key, "list_downloads", subscription_id=sub["id"], state=["done"], limit=50)
        assert len(done["downloads"]) == 20 and {d["quality"] for d in done["downloads"]} == {"low"}
