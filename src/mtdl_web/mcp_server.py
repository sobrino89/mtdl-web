"""MCP server: assistants such as Claude search the media libraries, subscribe to shows and
download through mtdl-web.

Streamable HTTP at ``/mcp`` with the official SDK ``mcp``, stateless and answering in JSON: every request stands
alone, no sessions to keep, nothing streamed. Off until the setting ``mcp_enabled`` is on, and then only with an API
key (``X-Api-Key`` or ``Authorization: Bearer``), in every access mode. No cookie counts here, so a web page cannot
borrow a signed-in browser. A read key gets the reading tools only, a write key all of them.

The tools are a client of the REST API inside the process: the same validation, errors and rights as a script
with that key, no second logic. They only trim the answers to what an assistant needs (no stream addresses).
"""

import collections
import contextlib
import datetime as dt
import json
import logging
from collections.abc import AsyncIterator, Mapping
from typing import Annotated, Any, Literal

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field
from starlette.datastructures import Headers
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from mtdl_web import __version__, auth
from mtdl_web.api import ConditionIO, NamingIn, SubscriptionIn, SubscriptionSaveIn
from mtdl_web.runtime import Runtime

log = logging.getLogger(__name__)

PATH = "/mcp"
SHORT = 300  # characters of a description in lists; get_film has all of it
SEEN = 1000  # films from MediathekViewWeb searches kept for get_film and enqueue_download

INSTRUCTIONS = """\
mtdl-web searches the media libraries (Mediatheken) of the German public broadcasters (ARD, ZDF, arte, 3sat and
more) and downloads from them; a subscription loads new episodes of a show by itself. Titles, topics and
descriptions are German.

Find films with search (film ids come from its results) and look at one with get_film. A single film:
enqueue_download, then get_download or list_downloads for the progress. A show: build a draft, check it with
preview_subscription (which episodes it would load, and why the others are left out), then create_subscription.
get_status shows what runs now and the bandwidth limit in effect; check it (or set one with update_settings) before
starting many downloads. cancel_downloads stops what was started, requeue_subscription loads cancelled or failed
episodes again. Tools that change something need an API key that may write; a read-only key does not get them."""

FilmId = Annotated[str, Field(pattern=r"^\d{1,20}$", description="A film id from search.")]
READS = ToolAnnotations(read_only_hint=True, open_world_hint=False)
CHANGES = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)

QUERY_SYNTAX = """\
The query uses the MediathekViewWeb syntax: plain words search topic and title (word prefixes, case does not
matter); !channel (!zdf), #topic (#tatort), +title, *description, >minutes and <minutes for the length. Words after
one selector joined by commas must all occur (#sturm,der,liebe); the same selector twice means either (!ard !zdf)."""

DRAFT = """\
A subscription draft: query finds the candidates (search syntax). conditions narrow them down, all must hold, each
{field, operator, value, regex (default false), enabled (default true)}:
- topic, title, description: is, is_not, contains, not_contains with a text. With regex true only is (matches) and
  is_not (does not match), search semantics, anchors decide: title is regex "\\(\\d+\\)$" keeps numbered episodes.
- channel: any_of, none_of with a list of channel names (["ZDF", "3sat"]).
- date: after, before with a day (YYYY-MM-DD). duration: at_least, at_most with minutes.
naming says where files go: scheme season_episode ("(S02/E03)" in the title, else the number "(39)" as season 1,
else the date), episode_number, date (default) or template; show and folder replace the show name (default: the
topic), season fixes the season. Into a series library under TMDB's names instead: target "library", library_id
(list_libraries) and tmdb_id (find_series). Optional: quality hd/normal/low, subtitles, valid_until (last day,
YYYY-MM-DD) with on_expiry pause or delete, state active or paused. special_versions: versions with audio
description, sign language, "Hörfassung" or "klare Sprache" are left out as the setting says ("setting", default:
out), "exclude" or "include" (a subscription for exactly such a version needs "include")."""


def presented_key(headers: Mapping[str, str]) -> str:
    """The API key a request carries: X-Api-Key, else a bearer token (what MCP clients offer)."""
    key = headers.get(auth.API_KEY_HEADER, "")
    if key:
        return key.strip()
    kind, _, token = headers.get("authorization", "").partition(" ")
    return token.strip() if kind.lower() == "bearer" else ""


def _problem(response: httpx.Response) -> str:
    try:
        data: Any = response.json()
    except ValueError:
        data = response.text
    detail = data.get("detail", data) if isinstance(data, dict) else data
    text = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)
    return f"mtdl-web answered {response.status_code}: {text[:1000]}"


class Rest:
    """The REST API inside the process, called with the caller's key."""

    def __init__(self, app: ASGIApp) -> None:
        transport = httpx.ASGITransport(app=app)
        self._client = httpx.AsyncClient(transport=transport, base_url="http://mtdl-web/api/v1")

    async def __call__(
        self,
        ctx: Context,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        body: Any = None,
    ) -> Any:
        key = presented_key(ctx.headers or {})
        response = await self._client.request(
            method, path, params=params, json=body, headers={auth.API_KEY_HEADER: key}
        )
        if response.status_code >= 400:
            raise ToolError(_problem(response))
        return response.json() if response.content else None

    async def close(self) -> None:
        await self._client.aclose()


# -- answers trimmed for an assistant -------------------------------------------------------------------------------


def _short(text: str) -> str:
    return text if len(text) <= SHORT else text[: SHORT - 1].rstrip() + "…"


def _film(f: Mapping[str, Any], *, full: bool = False) -> dict[str, Any]:
    qualities = [q for q, field in (("hd", "url_hd"), ("normal", "url"), ("low", "url_low")) if f.get(field)]
    out: dict[str, Any] = {
        "id": f["id"],
        "channel": f["channel"],
        "topic": f["topic"],
        "title": f["title"],
        "start": f["start"],
        "minutes": round(f["duration"] / 60) if f.get("duration") else None,
        "qualities": qualities,
        "subtitles": bool(f.get("url_subtitle")),
        "description": f["description"] if full else _short(f["description"]),
    }
    if full:
        out["website"] = f.get("url_website")
        out["bytes"] = f.get("size")
    if f.get("geo"):
        out["geo"] = f["geo"]  # only these countries may load it
    status = f.get("status") or {}
    if status.get("download_id") is not None:
        out["download"] = {
            "id": status["download_id"],
            "state": status["state"],
            "same_broadcast_other_channel": status["duplicate"],
        }
    if status.get("subscription_id") is not None:
        out["subscription"] = {"id": status["subscription_id"], "name": status["subscription_name"]}
    return out


def _download(d: Mapping[str, Any]) -> dict[str, Any]:
    film = d["film"]
    out: dict[str, Any] = {
        "id": d["id"],
        "state": d["state"],
        "film_id": film["id"],
        "channel": film["channel"],
        "topic": film["topic"],
        "title": film["title"],
        "file": d["file"],
        "quality": d["quality"],
        "subscription_id": d["subscription_id"],
        "created_at": d["created_at"],
        "finished_at": d["finished_at"],
    }
    if d["state"] == "running":
        total = d["bytes_total"]
        out["progress"] = round(d["bytes_done"] / total, 3) if total else None
        out["bytes_per_second"] = d["speed"]
        out["phase"] = d["phase"]
    if d["state"] == "queued":
        out["position"] = d["position"]
        out["retry_at"] = d["retry_at"]
    if d["error"]:
        out["error"] = d["error"]
        out["error_code"] = d["error_code"]
    return out


def _preview_item(i: Mapping[str, Any]) -> dict[str, Any]:
    film = i["film"]
    out: dict[str, Any] = {
        "film_id": film["id"],
        "channel": film["channel"],
        "topic": film["topic"],
        "title": film["title"],
        "start": film["start"],
        "status": i["status"],
    }
    if i["excluded_by"] is not None:
        out["excluded_by_condition"] = i["excluded_by"]
    if i["path"]:
        out["path"] = i["path"]
    if episode := i.get("episode"):
        out["episode"] = f"S{episode['season']:02d}E{episode['number']:02d} {episode['name']}"
    if i.get("reason"):
        out["reason"] = i["reason"]
    if i["duplicate_of"]:
        out["duplicate_of"] = i["duplicate_of"]
    if i.get("in_library"):
        out["in_library"] = True
    if i.get("special"):
        out["special_version"] = i["special"]
    return out


def _draft_of(saved: Mapping[str, Any]) -> dict[str, Any]:
    return {name: saved[name] for name in SubscriptionIn.model_fields}


def _mbit(byte_per_second: float) -> float:
    return round(byte_per_second * 8 / 1e6, 2)


class SubscriptionChanges(BaseModel):
    """What to change in a saved subscription; fields left out stay as they are."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    query: str | None = None
    include_description: bool | None = None
    source: Literal["filmlist", "mediathekviewweb"] | None = None
    conditions: list[ConditionIO] | None = Field(default=None, description="Replaces all conditions.")
    naming: NamingIn | None = None
    quality: Literal["hd", "normal", "low"] | None = Field(default=None, description="null: as in the settings.")
    subtitles: bool | None = Field(default=None, description="null: as in the settings.")
    state: Literal["active", "paused"] | None = None
    valid_until: dt.date | None = Field(default=None, description="The last day; null removes it.")
    on_expiry: Literal["pause", "delete"] | None = None
    special_versions: Literal["setting", "exclude", "include"] | None = None


class SettingsChanges(BaseModel):
    """Settings to change; fields left out stay as they are."""

    model_config = ConfigDict(extra="forbid")

    bandwidth_limit_mbit: float | None = Field(
        default=None, ge=0, description="Limit for all downloads together in Mbit/s; 0 = unlimited."
    )
    bandwidth_enabled: bool | None = Field(default=None, description="Switch the limit on or off; the value stays.")
    quality: Literal["hd", "normal", "low"] | None = Field(
        default=None, description="Preferred quality; a film without it loads the next lower one."
    )
    subtitles: bool | None = None
    parallel_downloads: int | None = Field(default=None, ge=1, le=10)

    def to_settings(self) -> dict[str, Any]:
        out = {k: v for k, v in self.model_dump().items() if v is not None and k != "bandwidth_limit_mbit"}
        if self.bandwidth_limit_mbit is not None:
            out["bandwidth_limit"] = round(self.bandwidth_limit_mbit * 1e6 / 8)  # stored in byte/s
        return out


# -- the tools ------------------------------------------------------------------------------------------------------


class Mcp:
    """Two servers on the same tools: one with the reading tools for read keys, one with all for write keys."""

    def __init__(self, app: ASGIApp) -> None:
        self.rest = Rest(app)
        # Films from MediathekViewWeb exist only in its answers; enqueue_download needs them whole.
        self._seen: collections.OrderedDict[str, dict[str, Any]] = collections.OrderedDict()
        self.read = self._server(write=False)
        self.write = self._server(write=True)

    @contextlib.asynccontextmanager
    async def running(self) -> AsyncIterator[None]:
        async with self.read.session_manager.run(), self.write.session_manager.run():
            try:
                yield
            finally:
                await self.rest.close()

    async def _pending(self, ctx: Context, subscription_id: int) -> dict[str, Any]:
        """What of a subscription waits or loads now, in the order it starts."""
        params = {"state": ["running", "queued"], "subscription": subscription_id, "order": "queue", "limit": 100}
        found = await self.rest(ctx, "GET", "/downloads", params=params)
        return {"pending": [_download(d) for d in found["items"]]}

    def _remember(self, films: list[dict[str, Any]]) -> None:
        for film in films:
            self._seen[film["id"]] = film
            self._seen.move_to_end(film["id"])
        while len(self._seen) > SEEN:
            self._seen.popitem(last=False)

    def _server(self, *, write: bool) -> MCPServer:
        rest = self.rest
        server = MCPServer(
            "mtdl-web",
            title="mtdl-web",
            instructions=INSTRUCTIONS,
            version=__version__,
            website_url="https://github.com/sobrino89/mtdl-web",
        )

        @server.tool(annotations=READS, description=f"Search films in mtdl-web's film list.\n\n{QUERY_SYNTAX}")
        async def search(
            ctx: Context,
            query: str,
            include_description: Annotated[bool, Field(description="Search the descriptions too.")] = False,
            limit: Annotated[int, Field(ge=1, le=100)] = 20,
            offset: Annotated[int, Field(ge=0)] = 0,
        ) -> dict[str, Any]:
            params = {"q": query, "description": include_description, "status": True, "limit": limit, "offset": offset}
            found = await rest(ctx, "GET", "/search", params=params)
            if found["source"] != "filmlist":
                self._remember(found["films"])
            return {"total": found["total"], "films": [_film(f) for f in found["films"]]}

        @server.tool(annotations=READS)
        async def get_film(ctx: Context, film_id: FilmId) -> dict[str, Any]:
            """One film with its whole description, the broadcaster's page and the size."""
            film = self._seen.get(film_id) or await rest(ctx, "GET", f"/films/{film_id}")
            return _film(film, full=True)

        @server.tool(annotations=READS)
        async def list_subscriptions(ctx: Context) -> dict[str, Any]:
            """All subscriptions with their query, conditions, naming, state and the last check."""
            return {"subscriptions": await rest(ctx, "GET", "/subscriptions")}

        @server.tool(
            annotations=READS,
            description=f"""\
What a subscription would load now, film by film: status load (would be loaded), exists (loaded before), excluded
(excluded_by_condition says which condition, counted from 0; one past the last is the special versions step, the
film then names its special_version), duplicate (same broadcast elsewhere), skipped,
failed, cancelled, unavailable, unmatched (no TMDB episode, see reason). Give subscription_id for a saved
subscription or draft for a new one; nothing is saved.

{DRAFT}""",
        )
        async def preview_subscription(
            ctx: Context,
            subscription_id: int | None = None,
            draft: SubscriptionIn | None = None,
            limit: Annotated[int, Field(ge=1, le=500, description="Films listed at most; counts cover all.")] = 50,
        ) -> dict[str, Any]:
            if (subscription_id is None) == (draft is None):
                raise ToolError("Give either subscription_id (a saved subscription) or draft (a new one).")
            params: dict[str, Any] = {}
            if draft is not None:
                body = draft.model_dump(mode="json")
            else:
                body = _draft_of(await rest(ctx, "GET", f"/subscriptions/{subscription_id}"))
                params["subscription"] = subscription_id
            preview = await rest(ctx, "POST", "/subscriptions/preview", params=params, body=body)
            items = preview["items"]
            return {
                "total": preview["total"],
                "counts": preview["counts"],
                "steps": preview["steps"],
                "films": [_preview_item(i) for i in items[:limit]],
                "films_not_listed": max(0, len(items) - limit),
            }

        @server.tool(annotations=READS)
        async def list_downloads(
            ctx: Context,
            state: list[Literal["queued", "running", "done", "failed", "cancelled"]] | None = None,
            subscription_id: int | None = None,
            limit: Annotated[int, Field(ge=1, le=200)] = 20,
        ) -> dict[str, Any]:
            """Downloads, newest first, optionally one subscription's only; counts are per state over all
            downloads. A failed one has error and error_code. file is where mtdl-web saved the video: someone may
            have moved or renamed it since."""
            params: dict[str, Any] = {"limit": limit}
            if state:
                params["state"] = state
            if subscription_id is not None:
                params["subscription"] = subscription_id
            found = await rest(ctx, "GET", "/downloads", params=params)
            return {"counts": found["counts"], "downloads": [_download(d) for d in found["items"]]}

        @server.tool(annotations=READS)
        async def get_download(ctx: Context, download_id: int) -> dict[str, Any]:
            """One download: state, progress while running, the error if it failed, and file, where mtdl-web saved
            the video (it may have been moved or renamed since)."""
            return _download(await rest(ctx, "GET", f"/downloads/{download_id}"))

        @server.tool(annotations=READS)
        async def get_status(ctx: Context) -> dict[str, Any]:
            """What mtdl-web does now: running downloads with progress and speed, the counts per state, the
            bandwidth limit in effect (null: unlimited; source normal, or schedule while a time window applies),
            the film list and problems such as a folder that cannot be written."""
            s = await rest(ctx, "GET", "/status")
            limit, films, downloads = s["limit"], s["filmlist"], s["downloads"]
            running = [
                {
                    "id": r["id"],
                    "topic": r["topic"],
                    "title": r["title"],
                    "progress": round(r["done"] / r["total"], 3) if r["total"] else None,
                    "mbit_per_second": _mbit(r["speed"]),
                    "phase": r["phase"],
                }
                for r in downloads["running"]
            ]
            return {
                "downloads": {
                    "counts": downloads["counts"],
                    "running": running,
                    "mbit_per_second": _mbit(downloads["speed"]),
                },
                "limit": {
                    "mbit_per_second": _mbit(limit["rate"]) if limit["rate"] else None,
                    "source": limit["source"],
                    "until": limit["until"],
                },
                "filmlist": {
                    "films": films["films"],
                    "updating": films["running"],
                    "created": (films["last_import"] or {}).get("created"),
                    "error": films["last_error"],
                },
                "subscriptions": s["subscriptions"],
                "problems": s["problems"],
            }

        @server.tool(annotations=READS)
        async def get_settings(ctx: Context) -> dict[str, Any]:
            """The settings: bandwidth_limit in byte/s (bandwidth_enabled switches it), the limit schedule,
            parallel_downloads, quality, subtitles, naming of single downloads, the search source and more."""
            settings: dict[str, Any] = await rest(ctx, "GET", "/settings")
            return settings

        @server.tool(annotations=READS)
        async def list_libraries(ctx: Context) -> dict[str, Any]:
            """The libraries (series or movies) that subscriptions and downloads can load into."""
            return {"libraries": await rest(ctx, "GET", "/libraries")}

        @server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True))
        async def find_series(ctx: Context, name: str) -> dict[str, Any]:
            """Series on TMDB by name, for a subscription into a series library (naming.tmdb_id). Needs a TMDB
            token in mtdl-web."""
            found = await rest(ctx, "GET", "/metadata/series", params={"q": name})
            return {"series": [{**s, "overview": _short(s["overview"])} for s in found]}

        if not write:
            return self._http(server)

        @server.tool(
            annotations=CHANGES,
            description=f"""\
Save a subscription: mtdl-web checks it after every film list update and loads what matches. Check the draft
with preview_subscription first. skip_existing true leaves what matches today alone and loads only episodes that
appear from now on.

{DRAFT}""",
        )
        async def create_subscription(ctx: Context, subscription: SubscriptionSaveIn) -> dict[str, Any]:
            saved: dict[str, Any] = await rest(ctx, "POST", "/subscriptions", body=subscription.model_dump(mode="json"))
            return saved

        @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True))
        async def set_subscription_state(
            ctx: Context, subscription_id: int, state: Literal["active", "paused"]
        ) -> dict[str, Any]:
            """Pause a subscription or let it search again."""
            body = {**_draft_of(await rest(ctx, "GET", f"/subscriptions/{subscription_id}")), "state": state}
            saved: dict[str, Any] = await rest(ctx, "PUT", f"/subscriptions/{subscription_id}", body=body)
            return saved

        @server.tool(
            annotations=CHANGES,
            description=f"""\
Change a saved subscription: give only what changes, the rest stays (valid_until null removes the last day,
conditions replace all conditions). To pause or resume, set_subscription_state is shorter.

{DRAFT}""",
        )
        async def update_subscription(
            ctx: Context, subscription_id: int, changes: SubscriptionChanges
        ) -> dict[str, Any]:
            saved = await rest(ctx, "GET", f"/subscriptions/{subscription_id}")
            body = {**_draft_of(saved), **changes.model_dump(mode="json", exclude_unset=True)}
            updated: dict[str, Any] = await rest(ctx, "PUT", f"/subscriptions/{subscription_id}", body=body)
            return updated

        @server.tool(annotations=CHANGES)
        async def check_subscription(ctx: Context, subscription_id: int) -> dict[str, Any]:
            """Check a subscription now instead of after the next film list update; queues what it finds and lists
            what of the subscription waits or loads now."""
            result = await rest(ctx, "POST", f"/subscriptions/{subscription_id}/check")
            return {"queued": result["queued"], **await self._pending(ctx, subscription_id)}

        @server.tool(annotations=CHANGES)
        async def requeue_subscription(ctx: Context, subscription_id: int) -> dict[str, Any]:
            """Load a subscription's cancelled and failed episodes again: they leave the history and are queued
            anew with the subscription's naming and quality of today. Episodes loaded fine stay as they are."""
            result = await rest(ctx, "POST", f"/subscriptions/{subscription_id}/requeue")
            return {**result, **await self._pending(ctx, subscription_id)}

        @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False))
        async def cancel_downloads(
            ctx: Context,
            download_ids: list[int] | None = None,
            subscription_id: int | None = None,
            everything: Annotated[bool, Field(description="Cancel all that waits or loads.")] = False,
        ) -> dict[str, Any]:
            """Stop downloads that wait or load: the ones named by id, a subscription's, or with everything true
            all of them. Partial files are removed; the history keeps them as cancelled (requeue_subscription or
            enqueue_download with force load them again). Pausing a subscription does not stop its downloads."""
            if download_ids:
                cancelled: list[int] = []
                refused: list[dict[str, Any]] = []
                for download_id in download_ids:
                    try:
                        await rest(ctx, "POST", f"/downloads/{download_id}/cancel")
                        cancelled.append(download_id)
                    except ToolError as exc:  # finished meanwhile, or no such download
                        refused.append({"id": download_id, "reason": str(exc)})
                return {"cancelled": cancelled, "not_cancelled": refused}
            if subscription_id is None and not everything:
                raise ToolError("Name download_ids, a subscription_id, or set everything true to cancel all.")
            params = {"subscription": subscription_id} if subscription_id is not None else {}
            result: dict[str, Any] = await rest(ctx, "POST", "/downloads/cancel", params=params)
            return result

        @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True))
        async def update_settings(ctx: Context, changes: SettingsChanges) -> dict[str, Any]:
            """Change the bandwidth limit (Mbit/s, for all downloads together, live), the preferred quality,
            subtitles or how many downloads run at once. A limit schedule from the settings page may set another
            limit at times; get_status shows the one in effect."""
            body = changes.to_settings()
            if not body:
                raise ToolError("Nothing to change.")
            settings: dict[str, Any] = await rest(ctx, "PATCH", "/settings", body=body)
            return settings

        @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True))
        async def enqueue_download(
            ctx: Context,
            film_id: FilmId,
            quality: Literal["hd", "normal", "low"] | None = None,
            subtitles: bool | None = None,
            force: Annotated[bool, Field(description="Load even if the broadcast was loaded before.")] = False,
        ) -> dict[str, Any]:
            """Queue one film (an id from search) into the download folder, named after the settings. Quality and
            subtitles default to the settings."""
            film = self._seen.get(film_id)
            body: dict[str, Any] = {"quality": quality, "subtitles": subtitles, "force": force}
            body |= {"film": film} if film else {"film_id": film_id}
            return _download(await rest(ctx, "POST", "/downloads", body=body))

        return self._http(server)

    @staticmethod
    def _http(server: MCPServer) -> MCPServer:
        # Stateless with JSON answers; the host check of the SDK guards servers without a key, /mcp always has one.
        server.streamable_http_app(
            stateless_http=True,
            json_response=True,
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )
        return server


class Endpoint:
    """The route /mcp: switched on, a valid key, then the servers its scope allows."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        state = scope["app"].state
        runtime: Runtime = state.runtime
        response: Response
        if not runtime.state.settings().mcp_enabled:
            response = JSONResponse({"detail": {"code": "mcp_disabled"}}, status_code=404)
        else:
            plain = presented_key(Headers(scope=scope))
            key = runtime.access.check_key(plain) if plain else None
            if key is not None:
                mcp: Mcp = state.mcp
                server = mcp.write if key.scope == "write" else mcp.read
                await server.session_manager.handle_request(scope, receive, send)
                return
            code = "api_key_invalid" if plain else "api_key_missing"
            response = JSONResponse(
                {"detail": {"code": code}}, status_code=401, headers={"WWW-Authenticate": 'Bearer realm="mtdl-web"'}
            )
        await response(scope, receive, send)
