"""The web application: API under /api, the built UI (if present) at /."""

import contextlib
import logging
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders
from starlette.responses import Response
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mtdl_web import __version__
from mtdl_web.api import access, health, v1
from mtdl_web.config import Settings
from mtdl_web.mcp_server import PATH as MCP_PATH
from mtdl_web.mcp_server import Endpoint, Mcp
from mtdl_web.runtime import Runtime

log = logging.getLogger(__name__)

UI_DIR = Path(__file__).parent / "ui"

# The UI loads no code from elsewhere; videos for the preview come straight from the broadcasters' CDNs: as a
# file for the video element, or for HLS through hls.js, which fetches playlist and segments (connect-src) and plays
# them from a MediaSource (blob:).
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:;"
    " media-src 'self' https: http: blob:; connect-src 'self' https: http:; object-src 'none'; base-uri 'none';"
    " form-action 'self'; frame-ancestors 'none'"
)
# Swagger UI starts itself with an inline script.
DOCS_CSP = CSP.replace("script-src 'self'", "script-src 'self' 'unsafe-inline'")
FAVICON = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32'"
    " rx='7.5' fill='%23302c28'/%3E%3Ccircle cx='10.5' cy='11' r='3.6' fill='%23d84a3a'/%3E%3C/svg%3E"
)

# The OpenAPI document is the API reference, also on the project website (tools/docs_data.py).
API_DESCRIPTION = """\
Everything mtdl-web's own interface does goes through this API, so scripts and other services can do it too.

**Base path** `/api/v1`, JSON in and out. Film ids travel as strings: JavaScript numbers lose precision beyond 2^53.

**Authentication.** Send an API key in the header `X-Api-Key`; keys are created in the settings under *Access*. A
read key may call every `GET` and the previews (`POST` routes that change nothing), a write key everything. A read
key asking for a change gets `403` with the code `api_key_read_only`. Without a key the access mode decides; with a
password that is the browser's session cookie, which is meant for the interface, not for scripts.

**Errors** carry an object in `detail` with a machine-readable `code`, sometimes with more fields:
`{"detail": {"code": "subscription_not_found"}}`, `{"detail": {"code": "library_in_use", "subscriptions": [3]}}`.
Input that does not fit the schema answers `422` with FastAPI's list of validation errors in `detail`.
A change a browser marks as coming from another site is refused with `403` and the code `cross_site`; scripts send
no such headers.

Events reach other services as webhooks or hook scripts; assistants use the MCP server at `/mcp` (off by default).
"""
OPENAPI_TAGS = [
    {"name": "search", "description": "Search the film list and look at single films."},
    {"name": "filmlist", "description": "The film list of the MediathekView project: its state, an update now."},
    {"name": "downloads", "description": "Queue and history: queue a film, cancel, retry, reorder."},
    {"name": "subscriptions", "description": "Rules that load a show's episodes by themselves: preview, check."},
    {"name": "libraries", "description": "Series and movie libraries, and the TMDB lookups that name their files."},
    {
        "name": "settings",
        "description": "Settings, folder checks, naming previews, TMDB and Plex, hook scripts, the takeover from"
        " MediathekView.",
    },
    {"name": "webhooks", "description": "Events sent to other services."},
    {"name": "access", "description": "Sign-in, password and API keys."},
    {"name": "system", "description": "Health, version, status and system information."},
]


class SecurityHeaders:
    """Headers for every response: no sniffing, no framing, no referrer (links to broadcaster pages should not
    carry the address of a LAN service); HTML pages also get a content security policy."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        docs = scope.get("path") == "/api/docs"

        async def with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("X-Frame-Options", "DENY")
                headers.setdefault("Referrer-Policy", "no-referrer")
                if headers.get("content-type", "").startswith("text/html"):
                    headers.setdefault("Content-Security-Policy", DOCS_CSP if docs else CSP)
            await send(message)

        await self.app(scope, receive, with_headers)


class UiFiles(StaticFiles):
    """The built UI. Its file names carry no hash, so browsers revalidate (ETag) instead of guessing freshness:
    after an update everyone gets the new UI on the next load."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    background: bool = True,
    ui_dir: Path = UI_DIR,
) -> FastAPI:
    """``transport`` replaces the network in tests; ``background`` False keeps the scheduler off."""

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        rt = Runtime.create(settings, transport)
        app.state.runtime = rt
        app.state.mcp = mcp = Mcp(app)
        if background:
            rt.start()
        log.info("mtdl-web %s ready on port %d", __version__, settings.port)
        try:
            async with mcp.running():
                yield
        finally:
            await rt.close()

    app = FastAPI(
        title="mtdl-web",
        version=__version__,
        summary="Search, download and subscribe to the German public broadcasters' media libraries.",
        description=API_DESCRIPTION,
        openapi_tags=OPENAPI_TAGS,
        license_info={"name": "GPL-3.0-or-later", "identifier": "GPL-3.0-or-later"},
        generate_unique_id_function=lambda route: route.name,  # operation ids: the function names
        lifespan=lifespan,
        docs_url=None,  # served below with Swagger UI from our own files, not from a CDN
        redoc_url=None,
        openapi_url="/api/v1/openapi.json",
    )
    app.add_middleware(SecurityHeaders)

    @app.get("/api/docs", include_in_schema=False)
    def api_docs() -> HTMLResponse:
        return get_swagger_ui_html(
            openapi_url=app.openapi_url or "/api/v1/openapi.json",
            title="mtdl-web API",
            swagger_js_url="/api-docs/swagger-ui-bundle.js",
            swagger_css_url="/api-docs/swagger-ui.css",
            swagger_favicon_url=FAVICON,
            swagger_ui_parameters={"validatorUrl": None},  # the default would call validator.swagger.io
        )

    app.include_router(health)
    app.include_router(access)
    app.include_router(v1)
    app.router.routes.append(Route(MCP_PATH, Endpoint()))  # an ASGI app: every method, the SDK answers
    if (ui_dir / "index.html").exists():
        app.mount("/", UiFiles(directory=ui_dir, html=True), name="ui")
    return app
