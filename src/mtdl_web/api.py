"""REST API, version 1. Names are English; texts for people come from the UI's language catalogue.

Film ids are 63-bit integers and travel as strings: JavaScript numbers lose precision beyond 2^53.
"""

import asyncio
import contextlib
import dataclasses
import datetime as dt
import os
import platform
import shutil
import time
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field, ValidationError

from mtdl_web import __version__, auth, takeover
from mtdl_web.config import AuthMode
from mtdl_web.downloads import DownloadError
from mtdl_web.downloads.manager import Duplicate, Live
from mtdl_web.downloads.queue import Download, State
from mtdl_web.filmlist.parser import clean_url
from mtdl_web.filmlist.store import ImportResult
from mtdl_web.libraries import Kind, Library, LibraryDraft, LibraryInUse
from mtdl_web.library import (
    VIDEO_EXTENSIONS,
    LibraryUnavailable,
    id_folders,
    library_path,
    movie_path,
    preferred_folder,
)
from mtdl_web.library import unknown_placeholders as unknown_library_placeholders
from mtdl_web.metadata import EpisodeInfo, MetadataError, MovieInfo, SeriesInfo
from mtdl_web.models import Film, Scope, film_id
from mtdl_web.naming import Naming, Scheme, Target, target_path, unknown_placeholders
from mtdl_web.plex import PlexError
from mtdl_web.query import parse as parse_query
from mtdl_web.rules.engine import RuleSet
from mtdl_web.rules.model import RuleError
from mtdl_web.runtime import Runtime, write_test
from mtdl_web.sources import SourceError
from mtdl_web.special import SpecialVersions, excluded, special_version
from mtdl_web.state import AppSettings
from mtdl_web.subscriptions import (
    Subscription,
    SubscriptionDraft,
    condition_from_json,
    condition_to_json,
    naming_to_json,
)
from mtdl_web.webhooks import MAX_TEMPLATE, EventName, Webhook
from mtdl_web.webhooks import Config as WebhookConfig
from mtdl_web.webhooks import problem as webhook_problem


def runtime(request: Request) -> Runtime:
    rt: Runtime = request.app.state.runtime
    return rt


RuntimeDep = Annotated[Runtime, Depends(runtime)]

# -- access (auth.py) ------------------------------------------------------------------------------------------

UNSAFE = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# POST routes that change nothing (previews with a body): read-only API keys may call them too.
READING: set[Callable[..., Any]] = set()


def reading[F: Callable[..., Any]](endpoint: F) -> F:
    READING.add(endpoint)
    return endpoint


def _peer(request: Request) -> str | None:
    return request.client.host if request.client else None


def _outside(request: Request) -> str | None:
    return auth.outside(_peer(request), request.headers, request.headers.getlist)


def _same_site(request: Request) -> None:
    """Changes only from the own pages, in every mode: without this, any web page a visitor opens could drive an
    instance in the LAN (cross-site request forgery). Browsers say where a request comes from (Sec-Fetch-Site);
    older ones only send Origin, compared with the address asked for. Scripts send neither and pass."""
    if request.method not in UNSAFE:
        return
    site = request.headers.get("sec-fetch-site")
    if site is not None:
        if site not in ("same-origin", "none"):
            raise HTTPException(403, detail={"code": "cross_site"})
        return
    origin = request.headers.get("origin")
    if origin is None:
        return
    hosts = {request.headers.get("host", "").lower()}
    hosts |= {h.strip().lower() for v in request.headers.getlist("x-forwarded-host") for h in v.split(",")}
    if origin == "null" or urlsplit(origin).netloc.lower() not in hosts:
        raise HTTPException(403, detail={"code": "cross_site"})


def _https(request: Request) -> bool:
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return request.url.scheme == "https" or forwarded == "https"


def _set_session(request: Request, response: Response, token: str) -> None:
    response.set_cookie(
        auth.SESSION_COOKIE,
        token,
        max_age=auth.SESSION_DAYS * 86400,
        path="/",
        httponly=True,
        samesite="lax",
        secure=_https(request),
    )


def _who(rt: Runtime, request: Request, response: Response | None = None) -> auth.Via | None:
    """How the request is allowed in, None if it is not (API keys aside)."""
    access = rt.access
    if access.mode == "none":
        if (reason := _outside(request)) is not None:
            access.note_outside(reason)
        return "none"
    if access.mode == "proxy":
        return "proxy" if access.proxy_user(_peer(request), request.headers) else None
    if access.mode == "local" and _outside(request) is None:
        return "local"
    token = request.cookies.get(auth.SESSION_COOKIE, "")
    found = access.session(token)
    if found is None:
        return None
    if found[1] and response is not None:  # extended: the cookie lives as long again
        _set_session(request, response, token)
    return "session"


# The key as the OpenAPI document declares it (Swagger UI's "Authorize", the API reference); require_access checks it.
API_KEY = APIKeyHeader(
    name="X-Api-Key",
    scheme_name="api_key",
    description="An API key from the settings (Access). A read key may call GET and the previews, a write key all.",
    auto_error=False,
)


def require_access(
    request: Request, response: Response, rt: RuntimeDep, key: Annotated[str | None, Security(API_KEY)]
) -> None:
    """Every route of /api/v1 but the sign-in itself: an API key, else the mode's own way in. A read-only key gets
    GET and the previews."""
    _same_site(request)
    if key:
        found = rt.access.check_key(key)
        if found is None:
            raise HTTPException(401, detail={"code": "api_key_invalid"})
        if found.scope == "read" and request.method in UNSAFE and request.scope.get("endpoint") not in READING:
            raise HTTPException(403, detail={"code": "api_key_read_only"})
        return
    if _who(rt, request, response) is None:
        raise HTTPException(401, detail={"code": "setup_required" if rt.access.needs_setup() else "login_required"})


health = APIRouter(tags=["system"])
access = APIRouter(prefix="/api/v1/auth", tags=["access"])
v1 = APIRouter(prefix="/api/v1", dependencies=[Depends(require_access)])


class AuthStatusOut(BaseModel):
    mode: AuthMode
    authenticated: bool
    via: Literal["session", "local", "proxy", "none"] | None
    setup_required: bool  # no password yet: the setup asks for one, with the code from the container log
    password_set: bool
    password_from_env: bool  # MTDL_PASSWORD(_FILE): the UI cannot change it
    user: str | None  # mode proxy: who the proxy signed in
    # Mode none: a request looked like it came from outside within the last day (reason: peer, forwarded, proxy,
    # host, origin); the UI warns.
    outside: str | None
    first_run: bool  # the guide of the first start is still to come: the password is its first step


def _status(rt: Runtime, request: Request, response: Response | None = None) -> AuthStatusOut:
    access_ = rt.access
    via = _who(rt, request, response)
    seen = access_.outside_seen
    recent = seen is not None and seen[0] > time.time() - 86400
    return AuthStatusOut(
        mode=access_.mode,  # type: ignore[arg-type]
        authenticated=via is not None,
        via=via,
        setup_required=access_.needs_setup(),
        password_set=access_.has_password(),
        password_from_env=access_.password_from_env,
        user=access_.proxy_user(_peer(request), request.headers) if access_.mode == "proxy" else None,
        outside=seen[1] if seen is not None and recent and access_.mode == "none" else None,
        first_run=not rt.state.settings().setup_done,
    )


@access.get("/status", response_model=AuthStatusOut, summary="Sign-in state")
def get_auth_status(request: Request, response: Response, rt: RuntimeDep) -> AuthStatusOut:
    """Open without sign-in: the UI decides from it whether to show the setup, the sign-in or itself."""
    return _status(rt, request, response)


class SetupIn(BaseModel):
    code: str = Field(max_length=40)
    password: str = Field(max_length=auth.MAX_PASSWORD)


class LoginIn(BaseModel):
    password: str = Field(max_length=auth.MAX_PASSWORD)


class PasswordIn(BaseModel):
    current: str | None = Field(default=None, max_length=auth.MAX_PASSWORD)  # needed once a password exists
    password: str = Field(max_length=auth.MAX_PASSWORD)


def _throttled(rt: Runtime, request: Request) -> str:
    """The address failed attempts count for; 429 while it has to wait."""
    address = _peer(request) or ""
    if wait := rt.access.throttle.wait(address):
        raise HTTPException(429, detail={"code": "too_many_attempts", "retry_after": wait})
    return address


def _new_password(password: str) -> None:
    if len(password) < auth.MIN_PASSWORD:
        raise HTTPException(422, detail={"code": "password_too_short", "min": auth.MIN_PASSWORD})


def _password_mode(rt: Runtime) -> None:
    if rt.access.mode not in ("password", "local"):
        raise HTTPException(409, detail={"code": "no_password_mode"})


@access.post("/setup", response_model=AuthStatusOut, summary="Set the first password")
def post_setup(body: SetupIn, request: Request, response: Response, rt: RuntimeDep) -> AuthStatusOut:
    """The first password, with the one-time code from the container log."""
    _same_site(request)
    _password_mode(rt)
    if not rt.access.needs_setup():
        raise HTTPException(409, detail={"code": "already_set_up"})
    address = _throttled(rt, request)
    if not rt.access.check_setup_code(body.code):
        rt.access.throttle.failed(address)
        raise HTTPException(403, detail={"code": "setup_code_wrong"})
    _new_password(body.password)
    rt.access.set_password(body.password)
    rt.access.throttle.succeeded(address)
    _set_session(request, response, rt.access.create_session(request.headers.get("user-agent", ""), address))
    return _signed_in(rt, request)


def _signed_in(rt: Runtime, request: Request) -> AuthStatusOut:
    out = _status(rt, request)
    return out.model_copy(update={"authenticated": True, "via": out.via or "session"})


@access.post("/login", response_model=AuthStatusOut, summary="Sign in")
def post_login(body: LoginIn, request: Request, response: Response, rt: RuntimeDep) -> AuthStatusOut:
    _same_site(request)
    _password_mode(rt)
    if rt.access.needs_setup():
        raise HTTPException(409, detail={"code": "setup_required"})
    address = _throttled(rt, request)
    if not rt.access.check_password(body.password):
        rt.access.throttle.failed(address)
        raise HTTPException(403, detail={"code": "password_wrong"})
    rt.access.throttle.succeeded(address)
    _set_session(request, response, rt.access.create_session(request.headers.get("user-agent", ""), address))
    return _signed_in(rt, request)


@access.post("/logout", status_code=204, summary="Sign out")
def post_logout(request: Request, response: Response, rt: RuntimeDep) -> None:
    _same_site(request)
    if token := request.cookies.get(auth.SESSION_COOKIE):
        rt.access.end_session(token)
    response.delete_cookie(auth.SESSION_COOKIE, path="/")


@access.post("/password", response_model=AuthStatusOut, summary="Change the password")
def post_password(body: PasswordIn, request: Request, response: Response, rt: RuntimeDep) -> AuthStatusOut:
    """Change the password (or set the first one from a local request); other sessions end."""
    _same_site(request)
    _password_mode(rt)
    via = _who(rt, request)
    if via not in ("session", "local"):
        raise HTTPException(401, detail={"code": "login_required"})
    if rt.access.password_from_env:
        raise HTTPException(409, detail={"code": "password_from_env"})
    if rt.access.has_password():
        address = _throttled(rt, request)
        if not rt.access.check_password(body.current or ""):
            rt.access.throttle.failed(address)
            raise HTTPException(403, detail={"code": "password_wrong"})
    _new_password(body.password)
    rt.access.set_password(body.password)
    token = request.cookies.get(auth.SESSION_COOKIE, "")
    rt.access.end_other_sessions(token)
    if via == "local":  # no session yet: sign in, so the browser keeps access also from elsewhere
        _set_session(request, response, rt.access.create_session(request.headers.get("user-agent", ""), ""))
    return _status(rt, request)


@access.post("/sessions/end-others", response_model=dict[str, int], summary="End the other sessions")
def post_end_other_sessions(request: Request, rt: RuntimeDep) -> dict[str, int]:
    """Sign out everywhere else (a lost phone, a shared computer)."""
    _same_site(request)
    if _who(rt, request) is None:
        raise HTTPException(401, detail={"code": "login_required"})
    return {"ended": rt.access.end_other_sessions(request.cookies.get(auth.SESSION_COOKIE, ""))}


class FilmStatusOut(BaseModel):
    """What mtdl-web knows about a search hit: downloaded (also as the same broadcast on another channel),
    waiting, loading, and which subscription it would belong to."""

    download_id: int | None = None
    state: str | None = None
    progress: float | None = None  # 0..1 while loading
    duplicate: bool = False  # the download is the same broadcast from another channel
    subscription_id: int | None = None
    subscription_name: str | None = None


class FilmOut(BaseModel):
    id: str
    channel: str
    topic: str
    title: str
    description: str
    start: dt.datetime | None
    duration: int | None
    size: int | None
    url: str | None
    url_low: str | None
    url_hd: str | None
    url_subtitle: str | None
    url_website: str | None
    geo: list[str]
    status: FilmStatusOut | None = None

    @classmethod
    def of(cls, film: Film) -> "FilmOut":
        return cls(
            id=str(film.id),
            channel=film.channel,
            topic=film.topic,
            title=film.title,
            description=film.description,
            start=film.start,
            duration=film.duration,
            size=film.size,
            url=film.url,
            url_low=film.url_low,
            url_hd=film.url_hd,
            url_subtitle=film.url_subtitle,
            url_website=film.url_website,
            geo=list(film.geo),
        )


class SearchOut(BaseModel):
    source: str
    total: int
    films: list[FilmOut]


class ImportOut(BaseModel):
    kind: str
    list_id: str
    created: dt.datetime
    added: int
    changed: int
    removed: int
    total: int

    @classmethod
    def of(cls, result: ImportResult) -> "ImportOut":
        return cls(
            kind=result.kind,
            list_id=result.list_id,
            created=result.created,
            added=result.added,
            changed=result.changed,
            removed=result.removed,
            total=result.total,
        )


class ProgressOut(BaseModel):
    phase: Literal["download", "import"]
    done: int  # bytes downloaded, then films read
    total: int | None  # the size the server names, then the films of the last full list (none on the first)


class FilmlistOut(BaseModel):
    films: int
    last_import: ImportOut | None
    running: bool
    last_run: dt.datetime | None
    last_error: str | None
    next_run: dt.datetime | None
    backoff_until: dt.datetime | None
    progress: ProgressOut | None  # while an update runs


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    problems: list[str]


class InfoOut(BaseModel):
    version: str
    description_search: bool  # the UI shows the "also search descriptions" switch only when true
    timezone: str
    download_dir: str
    uid: int  # the process runs as uid:gid (PUID/PGID); the UI shows it when a folder is not writable
    gid: int
    workers: int
    tmdb: bool  # a TMDB token is set (environment or settings)


@health.get("/api/health", response_model=HealthOut, summary="Health check")
def get_health(rt: RuntimeDep, response: Response) -> HealthOut:
    """For the container health check: cheap, writes nothing (a write test would keep array disks awake).
    503 when degraded, so Docker marks the container unhealthy; the UI still works and shows the problems."""
    problems = [f"{name} not writable: {problem}" for name, problem in rt.writable.items() if problem]
    if rt.films.count() == 0 and rt.scheduler.status.last_error:
        problems.append(f"no film list: {rt.scheduler.status.last_error}")
    if problems:
        response.status_code = 503
    return HealthOut(status="degraded" if problems else "ok", version=__version__, problems=problems)


@v1.get("/info", response_model=InfoOut, summary="Server information", tags=["system"])
def get_info(rt: RuntimeDep) -> InfoOut:
    return InfoOut(
        version=__version__,
        description_search=rt.settings.index_description,
        timezone=str(rt.settings.timezone),
        download_dir=str(rt.settings.download_dir),
        uid=os.getuid(),
        gid=os.getgid(),
        workers=rt.settings.workers,
        tmdb=rt.tmdb.configured,
    )


class SystemOut(BaseModel):
    version: str
    python: str
    timezone: str
    config_dir: str
    download_dir: str
    uid: int
    gid: int
    workers: int
    films: int
    film_db_bytes: int
    state_db_bytes: int
    download_free_bytes: int | None  # None if the folder cannot be measured
    download_total_bytes: int | None
    writable: dict[str, str | None]


@v1.get("/system", response_model=SystemOut, summary="System overview", tags=["system"])
def get_system(rt: RuntimeDep) -> SystemOut:
    """What runs where, sizes and free space: for the settings page and bug reports (no secrets)."""
    free: int | None = None
    total: int | None = None
    with contextlib.suppress(OSError):
        usage = shutil.disk_usage(rt.settings.download_dir)
        free, total = usage.free, usage.total
    return SystemOut(
        version=__version__,
        python=platform.python_version(),
        timezone=str(rt.settings.timezone),
        config_dir=str(rt.settings.config_dir),
        download_dir=str(rt.settings.download_dir),
        uid=os.getuid(),
        gid=os.getgid(),
        workers=rt.settings.workers,
        films=rt.films.count(),
        film_db_bytes=rt.films.database_bytes(),
        state_db_bytes=rt.state.database_bytes(),
        download_free_bytes=free,
        download_total_bytes=total,
        writable=rt.writable,
    )


@v1.get("/filmlist", response_model=FilmlistOut, summary="Film list state", tags=["filmlist"])
def get_filmlist(rt: RuntimeDep) -> FilmlistOut:
    status = rt.scheduler.status
    last = rt.films.last_import()
    progress = rt.updater.progress
    return FilmlistOut(
        films=rt.films.count(),
        last_import=ImportOut.of(last) if last else None,
        running=status.running,
        last_run=status.last_run,
        last_error=status.last_error,
        next_run=status.next_run,
        backoff_until=rt.updater.backoff.not_before,
        progress=ProgressOut(phase=progress.phase, done=progress.done, total=progress.total) if progress else None,
    )


@v1.post("/filmlist/update", status_code=202, summary="Update the film list now", tags=["filmlist"])
def post_filmlist_update(rt: RuntimeDep) -> dict[str, str]:
    rt.scheduler.trigger()
    return {"status": "started"}


EVERY_STATE: tuple[State, ...] = ("running", "queued", "done", "failed", "cancelled")


def film_status(rt: Runtime, films: list[Film]) -> list[FilmStatusOut]:
    rules = [(s, RuleSet(s.conditions, timezone=rt.settings.timezone)) for s in rt.subscriptions.store.entries()]
    setting = rt.state.settings().exclude_special_versions
    out = []
    for film in films:
        status = FilmStatusOut()
        existing = rt.downloads.find_existing(film, EVERY_STATE)
        if existing is not None:
            status.download_id, status.state = existing.id, existing.state
            status.duplicate = existing.film.id != film.id
            live = rt.downloads.live.get(existing.id)
            if live is not None and live.total:
                status.progress = min(1.0, live.done / live.total)
        special = special_version(film) is not None
        match = next(
            (
                s
                for s, r in rules
                if r.evaluate(film).passed and not (special and excluded(s.special_versions, setting))
            ),
            None,
        )
        if match is not None:
            status.subscription_id, status.subscription_name = match.id, match.name
        out.append(status)
    return out


@v1.get("/search", response_model=SearchOut, summary="Search films", tags=["search"])
async def get_search(
    rt: RuntimeDep,
    q: str = "",
    scope: Scope = "topic_title",
    description: bool = False,
    source: Literal["filmlist", "mediathekviewweb"] | None = None,
    status: bool = False,
    limit: Annotated[int, Query(ge=1, le=2000)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SearchOut:
    """``q`` understands the MediathekViewWeb syntax: ``!sender``, ``#thema``, ``+titel``, ``*beschreibung``,
    ``>minuten``, ``<minuten``, commas join words that must all occur. ``scope`` all (or ``description``) also searches
    descriptions; ``status`` adds what mtdl-web knows per hit."""
    chosen = rt.source(source)
    if (description or scope == "all" or parse_query(q).descriptions) and not chosen.description_search:
        raise HTTPException(400, detail={"code": "description_search_unavailable"})
    try:
        result = await chosen.search(q, include_description=description, scope=scope, limit=limit, offset=offset)
    except SourceError as exc:
        raise HTTPException(502, detail={"code": "source_error", "message": str(exc)}) from exc
    films = [FilmOut.of(f) for f in result.films]
    if status:
        for out, film_state in zip(films, film_status(rt, result.films), strict=True):
            out.status = film_state
    return SearchOut(source=chosen.name, total=result.total, films=films)


@v1.get("/films/{film_id}", response_model=FilmOut, summary="Get a film", tags=["search"])
def get_film(rt: RuntimeDep, film_id: str) -> FilmOut:
    film = rt.films.get(int(film_id)) if film_id.isdigit() else None
    if film is None:
        raise HTTPException(404, detail={"code": "film_not_found"})
    return FilmOut.of(film)


@v1.get("/settings", response_model=AppSettings, summary="Get the settings", tags=["settings"])
def get_settings(rt: RuntimeDep) -> AppSettings:
    return rt.state.settings()


@v1.patch("/settings", response_model=AppSettings, summary="Change settings", tags=["settings"])
async def patch_settings(rt: RuntimeDep, changes: dict[str, Any]) -> AppSettings:
    try:
        updated = rt.state.update_settings(changes)
    except ValidationError as exc:
        raise HTTPException(422, detail=exc.errors(include_url=False, include_context=False)) from exc
    rt.apply_settings(updated)
    return updated


@v1.post("/checks/directories", summary="Test the folders for writing", tags=["settings"])
def post_directory_check(rt: RuntimeDep) -> dict[str, str | None]:
    """Write test on demand (settings page, first start)."""
    return rt.check_directories()


# -- downloads -------------------------------------------------------------------------------------------------------


class NamingIn(BaseModel):
    scheme: Scheme = "date"
    show: str | None = None
    folder: str | None = None
    season: int | None = Field(default=None, ge=0, le=9999)
    template: str | None = None
    # "library": straight into the library ``library_id`` under TMDB's names. A subscription loads into a
    # series library, ``tmdb_id`` is its series; a single download into a movie library, ``tmdb_id`` is the movie.
    # Without ``library_id`` the only library of that kind (the API of 0.1.0.dev16 to dev19 knew one).
    target: Target = "downloads"
    tmdb_id: int | None = Field(default=None, ge=1)
    library_id: int | None = Field(default=None, ge=1)

    def to_naming(self) -> Naming:
        library_id = self.library_id if self.target == "library" else None
        return Naming(
            self.scheme, self.show, self.folder, self.season, self.template, self.target, self.tmdb_id, library_id
        )


class FilmIn(BaseModel):
    """A film from a search result, for sources other than the local film list (MediathekViewWeb)."""

    channel: str
    topic: str
    title: str
    description: str = ""
    start: dt.datetime | None = None
    duration: int | None = None
    size: int | None = None
    url: str | None = None
    url_low: str | None = None
    url_hd: str | None = None
    url_subtitle: str | None = None
    url_website: str | None = None
    geo: list[str] = []

    def to_film(self) -> Film:
        raw_url = self.url or self.url_hd or self.url_low or ""
        return Film(
            id=film_id(self.channel, self.topic, self.title, raw_url),
            channel=self.channel,
            topic=self.topic,
            title=self.title,
            description=self.description,
            timestamp=int(self.start.timestamp()) if self.start else None,
            duration=self.duration,
            size=self.size,
            url=clean_url(self.url or ""),
            url_low=clean_url(self.url_low or ""),
            url_hd=clean_url(self.url_hd or ""),
            url_subtitle=clean_url(self.url_subtitle or ""),
            url_website=self.url_website,
            geo=tuple(self.geo),
        )


class NamingPreviewIn(BaseModel):
    naming: NamingIn
    normalize: bool | None = None  # None: as in the settings
    films: list[FilmIn] = Field(max_length=20)


@v1.post("/naming/preview", summary="Preview file names", tags=["settings"])
@reading
def post_naming_preview(rt: RuntimeDep, body: NamingPreviewIn) -> dict[str, list[str]]:
    """Target paths of example films under a naming scheme: the live example in the settings."""
    n = body.naming
    if n.scheme == "template" and (bad := unknown_placeholders(n.template or "")):
        raise HTTPException(422, detail={"code": "unknown_placeholders", "placeholders": bad})
    normalize = rt.state.settings().normalize_filenames if body.normalize is None else body.normalize
    naming = Naming(n.scheme, n.show, n.folder, n.season, n.template)
    timezone = rt.settings.timezone
    paths = [target_path(f.to_film(), naming, normalize=normalize, timezone=timezone) for f in body.films]
    return {"paths": [str(p) for p in paths]}


class DownloadIn(BaseModel):
    film_id: str | None = None  # from the local film list
    film: FilmIn | None = None  # or the film itself (MediathekViewWeb results)
    quality: Literal["hd", "normal", "low"] | None = None
    subtitles: bool | None = None
    naming: NamingIn | None = None
    force: bool = False  # load even if the broadcast was loaded before


class DownloadOut(BaseModel):
    id: int
    state: str
    film: FilmOut
    path: str  # relative to the library (library_id), else to the download folder
    file: str  # the video as the container sees it, absolute
    library_id: int | None
    quality: str
    subtitles: bool
    subscription_id: int | None
    bytes_done: int
    bytes_total: int | None
    speed: float | None  # byte/s while running
    phase: str | None
    error: str | None
    error_code: str | None  # geo, forbidden, gone, http, network, permission, disk_full, filesystem, mux, ...
    attempts: int
    retry_at: dt.datetime | None
    position: int  # queue order, lower starts first
    created_at: dt.datetime
    started_at: dt.datetime | None
    finished_at: dt.datetime | None

    @classmethod
    def of(cls, download: Download, live: Live | None, file: str) -> "DownloadOut":
        def when(value: int | None) -> dt.datetime | None:
            return None if value is None else dt.datetime.fromtimestamp(value, dt.UTC)

        return cls(
            id=download.id,
            state=download.state,
            film=FilmOut.of(download.film),
            path=download.path,
            file=file,
            library_id=download.library_id,
            quality=download.quality,
            subtitles=download.subtitles,
            subscription_id=download.subscription_id,
            bytes_done=live.done if live else download.bytes_done,
            bytes_total=live.total if live else download.bytes_total,
            speed=live.speed if live else None,
            phase=live.phase if live else None,
            error=download.error,
            error_code=download.error_code,
            attempts=download.attempts,
            retry_at=when(download.not_before) if download.state == "queued" else None,
            position=download.position,
            created_at=dt.datetime.fromtimestamp(download.created_at, dt.UTC),
            started_at=when(download.started_at),
            finished_at=when(download.finished_at),
        )


def download_out(rt: Runtime, download: Download) -> DownloadOut:
    return DownloadOut.of(download, rt.downloads.live.get(download.id), rt.downloads.file_of(download))


class DownloadListOut(BaseModel):
    counts: dict[str, int]
    items: list[DownloadOut]


@v1.get("/downloads", response_model=DownloadListOut, summary="List downloads", tags=["downloads"])
async def get_downloads(
    rt: RuntimeDep,
    state: Annotated[list[Literal["queued", "running", "done", "failed", "cancelled"]] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
    order: Literal["newest", "queue"] = "newest",
    subscription: int | None = None,
) -> DownloadListOut:
    """``order=queue`` lists in the order downloads start (for the queue), else newest first; ``subscription``
    only that subscription's (the counts stay those of all downloads)."""
    queue = rt.downloads.queue
    states = tuple(state) if state else None
    items = queue.entries(states, limit=limit, offset=offset, order=order, subscription_id=subscription)
    return DownloadListOut(counts=queue.counts(), items=[download_out(rt, d) for d in items])


@v1.post("/downloads/cancel", summary="Cancel downloads", tags=["downloads"])
async def post_downloads_cancel(rt: RuntimeDep, subscription: int | None = None) -> dict[str, list[int]]:
    """Cancels everything waiting or loading, or only one subscription's downloads. The ids allow an undo
    (``/downloads/{id}/retry``)."""
    return {"cancelled": await rt.downloads.cancel_all(subscription)}


@v1.post("/downloads", response_model=DownloadOut, status_code=201, summary="Queue a download", tags=["downloads"])
async def post_download(rt: RuntimeDep, body: DownloadIn) -> DownloadOut:
    film = _film_of(rt, body)
    target = await _download_target(rt, film, body.naming)
    try:
        download = await rt.downloads.enqueue(
            film,
            target.path,
            quality=body.quality,
            subtitles=body.subtitles,
            library_id=target.library.id if target.library else None,
            force=body.force,
        )
    except Duplicate as exc:
        detail = {"code": "duplicate", "existing_id": exc.existing.id, "state": exc.existing.state}
        raise HTTPException(409, detail=detail) from exc
    except DownloadError as exc:
        raise HTTPException(400, detail={"code": "not_downloadable", "message": str(exc)}) from exc
    return download_out(rt, download)


@dataclasses.dataclass(frozen=True, slots=True)
class _Target:
    path: PurePosixPath  # relative to the library, else to the download folder
    library: Library | None


def _film_of(rt: Runtime, body: "DownloadIn") -> Film:
    if body.film is not None:
        return body.film.to_film()
    if body.film_id and body.film_id.isdigit() and (found := rt.films.get(int(body.film_id))) is not None:
        return found
    raise HTTPException(404, detail={"code": "film_not_found"})


async def _download_target(rt: Runtime, film: Film, naming_in: NamingIn | None) -> _Target:
    """Where a single download goes: the download folder under a naming scheme, or a movie library under the TMDB
    movie's name. A series library needs the episode a subscription finds: not for single downloads."""
    settings = rt.state.settings()
    naming_in = naming_in or NamingIn(scheme=settings.naming_scheme, template=settings.naming_template)
    if naming_in.target != "library":
        if naming_in.scheme == "template" and (bad := unknown_placeholders(naming_in.template or "")):
            raise HTTPException(422, detail={"code": "unknown_placeholders", "placeholders": bad})
        naming = naming_in.to_naming()
        path = target_path(film, naming, normalize=settings.normalize_filenames, timezone=rt.settings.timezone)
        return _Target(path, None)
    library = _library_of(rt, naming_in.library_id, "movies")
    if library.kind != "movies":
        raise HTTPException(422, detail={"code": "library_single_download"})
    if naming_in.tmdb_id is None:
        raise HTTPException(422, detail={"code": "movie_missing"})
    try:
        movie = await rt.tmdb.movie(naming_in.tmdb_id)
    except MetadataError as exc:
        raise metadata_http_error(exc) from exc
    existing = preferred_folder(id_folders(library.path, movie), movie, library.template)
    absolute = movie_path(
        film,
        movie,
        library.template,
        root=library.path,
        existing=existing,
        normalize=library.normalize,
        timezone=rt.settings.timezone,
    )
    return _Target(absolute.relative_to(library.path), library)


class TargetOut(BaseModel):
    library_id: int | None
    path: str  # relative to the library, else to the download folder
    file: str  # absolute, as the container sees it
    exists: bool  # a file is there already: the download would load nothing
    # In a library: other videos in the target folder, the movie in another version (FileBot's .mkv, say).
    videos: list[str]


@v1.post("/downloads/target", response_model=TargetOut, summary="Preview where a download goes", tags=["downloads"])
@reading
async def post_download_target(rt: RuntimeDep, body: DownloadIn) -> TargetOut:
    """Where ``POST /downloads`` would put the film, without queueing it: the preview of the download dialog."""
    target = await _download_target(rt, _film_of(rt, body), body.naming)
    file = (target.library.path if target.library else rt.settings.download_dir) / target.path
    videos: list[str] = []
    if target.library is not None and file.parent.is_dir():
        found = (p for p in file.parent.iterdir() if p.suffix.lower() in VIDEO_EXTENSIONS and p.name != file.name)
        videos = sorted(p.name for p in found)
    return TargetOut(
        library_id=target.library.id if target.library else None,
        path=str(target.path),
        file=str(file),
        exists=file.exists(),
        videos=videos,
    )


@v1.get("/downloads/{download_id}", response_model=DownloadOut, summary="Get a download", tags=["downloads"])
async def get_download(rt: RuntimeDep, download_id: int) -> DownloadOut:
    download = rt.downloads.queue.get(download_id)
    if download is None:
        raise HTTPException(404, detail={"code": "download_not_found"})
    return download_out(rt, download)


@v1.post("/downloads/{download_id}/cancel", response_model=DownloadOut, summary="Cancel a download", tags=["downloads"])
async def post_download_cancel(rt: RuntimeDep, download_id: int) -> DownloadOut:
    if not await rt.downloads.cancel(download_id):
        raise HTTPException(409, detail={"code": "not_cancellable"})
    return await get_download(rt, download_id)


@v1.post("/downloads/{download_id}/retry", response_model=DownloadOut, summary="Retry a download", tags=["downloads"])
async def post_download_retry(rt: RuntimeDep, download_id: int) -> DownloadOut:
    if not rt.downloads.retry(download_id):
        raise HTTPException(409, detail={"code": "not_retryable"})
    return await get_download(rt, download_id)


class MoveIn(BaseModel):
    index: int = Field(ge=0)  # new place in the queue, 0 = starts next


@v1.post("/downloads/{download_id}/move", status_code=204, summary="Move a download in the queue", tags=["downloads"])
async def post_download_move(rt: RuntimeDep, download_id: int, body: MoveIn) -> None:
    """Changes the order of the queue; only waiting downloads can move."""
    if not rt.downloads.move(download_id, body.index):
        raise HTTPException(409, detail={"code": "not_queued"})


@v1.delete(
    "/downloads/{download_id}", status_code=204, summary="Remove a download from the history", tags=["downloads"]
)
async def delete_download(rt: RuntimeDep, download_id: int) -> None:
    """Removes the entry (history); files stay where they are."""
    if not rt.downloads.queue.delete(download_id):
        raise HTTPException(409, detail={"code": "not_deletable"})


# -- subscriptions ---------------------------------------------------------------------------------------------------


class ConditionIO(BaseModel):
    field: str
    operator: str
    value: Any
    regex: bool = False
    enabled: bool = True


class SubscriptionIn(BaseModel):
    name: str = ""
    query: str
    include_description: bool = False
    source: Literal["filmlist", "mediathekviewweb"] | None = None
    conditions: list[ConditionIO] = []
    naming: NamingIn = NamingIn()
    quality: Literal["hd", "normal", "low"] | None = None
    subtitles: bool | None = None
    state: Literal["active", "paused"] = "active"
    # The last day it searches; afterwards it pauses itself or is deleted (files and history stay).
    valid_until: dt.date | None = None
    on_expiry: Literal["pause", "delete"] = "pause"
    # Audio description, sign language and the like (special.py): as in the settings, always out, or loaded too.
    special_versions: SpecialVersions = "setting"

    def to_draft(self, *, require_name: bool) -> SubscriptionDraft:
        conditions = []
        for index, condition in enumerate(self.conditions):
            try:
                conditions.append(condition_from_json(condition.model_dump()))
            except RuleError as exc:
                detail = {"code": exc.code, "params": jsonable(exc.params), "condition": index}
                raise HTTPException(422, detail=detail) from exc
        if require_name and not self.name.strip():
            raise HTTPException(422, detail={"code": "name_missing"})
        if self.naming.scheme == "template" and (bad := unknown_placeholders(self.naming.template or "")):
            raise HTTPException(422, detail={"code": "unknown_placeholders", "placeholders": bad})
        return SubscriptionDraft(
            name=self.name.strip(),
            query=self.query,
            conditions=tuple(conditions),
            naming=self.naming.to_naming(),
            include_description=self.include_description,
            source=self.source,
            quality=self.quality,
            subtitles=self.subtitles,
            state=self.state,
            valid_until=self.valid_until,
            on_expiry=self.on_expiry,
            special_versions=self.special_versions,
        )


class SubscriptionSaveIn(SubscriptionIn):
    # Leave the episodes that match now alone: the subscription loads only what appears from now on.
    skip_existing: bool = False


def jsonable(params: dict[str, object]) -> dict[str, object]:
    return {k: v if isinstance(v, str | int | float | bool | None) else str(v) for k, v in params.items()}


class SubscriptionOut(SubscriptionIn):
    id: int
    created_at: dt.datetime
    updated_at: dt.datetime
    last_check: dt.datetime | None
    last_error: str | None
    last_queued: int | None
    stats: dict[str, Any]

    @classmethod
    def of(cls, sub: Subscription, stats: dict[str, Any] | None) -> "SubscriptionOut":
        def when(value: int | None) -> dt.datetime | None:
            return None if value is None else dt.datetime.fromtimestamp(value, dt.UTC)

        naming = naming_to_json(sub.naming)
        return cls(
            id=sub.id,
            name=sub.name,
            query=sub.query,
            include_description=sub.include_description,
            source=sub.source,  # type: ignore[arg-type]
            conditions=[ConditionIO(**condition_to_json(c)) for c in sub.conditions],
            naming=NamingIn(**naming),
            quality=sub.quality,  # type: ignore[arg-type]
            subtitles=sub.subtitles,
            state=sub.state,
            valid_until=sub.valid_until,
            on_expiry=sub.on_expiry,
            special_versions=sub.special_versions,
            created_at=dt.datetime.fromtimestamp(sub.created_at, dt.UTC),
            updated_at=dt.datetime.fromtimestamp(sub.updated_at, dt.UTC),
            last_check=when(sub.last_check),
            last_error=sub.last_error,
            last_queued=sub.last_queued,
            stats=stats or {},
        )


class ConditionResultOut(BaseModel):
    passed: bool
    code: str
    actual: str | int | None
    span: list[int] | None


class EpisodeOut(BaseModel):
    season: int
    number: int
    name: str
    air_date: dt.date | None
    runtime: int | None

    @classmethod
    def of(cls, e: EpisodeInfo) -> "EpisodeOut":
        return cls(season=e.season, number=e.number, name=e.name, air_date=e.air_date, runtime=e.runtime)


class PreviewItemOut(BaseModel):
    film: FilmOut
    status: str
    excluded_by: int | None
    path: str | None
    duplicate_of: str | None
    download_id: int | None
    results: list[ConditionResultOut]
    # Library target: the TMDB episode and the signals that found it (number, title, date and pairs), or
    # for "unmatched" why none fits: no_series, no_match, ambiguous, conflict, too_short.
    episode: EpisodeOut | None = None
    reason: str | None = None
    in_library: bool = False
    special: str | None = None  # the marker of a special version ("Audiodeskription"), excluded or not


class PreviewStepOut(BaseModel):
    index: int  # of the condition; the special versions step comes after the conditions (index = their number)
    enabled: bool
    before: int
    excluded: int
    remaining: int
    kind: Literal["condition", "special_versions"] = "condition"


class PreviewOut(BaseModel):
    total: int
    counts: dict[str, int]
    steps: list[PreviewStepOut]
    items: list[PreviewItemOut]


@v1.get("/subscriptions", response_model=list[SubscriptionOut], summary="List subscriptions", tags=["subscriptions"])
async def get_subscriptions(rt: RuntimeDep) -> list[SubscriptionOut]:
    stats = rt.subscriptions.store.stats()
    return [SubscriptionOut.of(s, stats.get(s.id)) for s in rt.subscriptions.store.entries()]


def _searchable(rt: Runtime, draft: SubscriptionDraft) -> None:
    """Descriptions (option or ``*`` selector) need the description index of the chosen source."""
    wanted = draft.include_description or parse_query(draft.query).descriptions
    if wanted and not rt.source(draft.source).description_search:
        raise HTTPException(400, detail={"code": "description_search_unavailable"})


def _library_of(rt: Runtime, library_id: int | None, kind: Kind) -> Library:
    """The library named, or the only one of ``kind`` when none is named."""
    if library_id is not None:
        library = rt.libraries.get(library_id)
        if library is None:
            raise HTTPException(422, detail={"code": "library_unavailable"})
        return library
    found = rt.libraries.entries(kind)
    if not found:
        raise HTTPException(422, detail={"code": "library_unavailable"})
    if len(found) > 1:
        raise HTTPException(422, detail={"code": "library_required"})
    return found[0]


def _still_valid(rt: Runtime, draft: SubscriptionDraft) -> None:
    """An active subscription whose last day is past would pause itself at once: extend or clear the day first."""
    today = dt.datetime.now(rt.settings.timezone).date()
    if draft.state == "active" and draft.valid_until is not None and draft.valid_until < today:
        raise HTTPException(422, detail={"code": "valid_until_past"})


def _library_ready(rt: Runtime, draft: SubscriptionDraft) -> SubscriptionDraft:
    """A subscription into a library needs a series library, a TMDB token and its series. Saved, it names its
    library: one without (API of 0.1.0.dev16 to dev19) gets the only series library."""
    naming = draft.naming
    if naming.target != "library":
        return draft
    library = _library_of(rt, naming.library_id, "series")
    if library.kind != "series":
        raise HTTPException(422, detail={"code": "library_kind"})
    if not rt.tmdb.configured:
        raise HTTPException(422, detail={"code": "tmdb_missing"})
    if naming.tmdb_id is None:
        raise HTTPException(422, detail={"code": "series_missing"})
    return dataclasses.replace(draft, naming=dataclasses.replace(naming, library_id=library.id))


def metadata_http_error(exc: MetadataError) -> HTTPException:
    status = {"no_token": 409, "not_found": 404}.get(exc.code, 502)
    return HTTPException(status, detail={"code": f"tmdb_{exc.code}", "message": str(exc)})


async def _keys_now(rt: Runtime, draft: SubscriptionDraft, skipped: frozenset[str] = frozenset()) -> list[str]:
    try:
        return await rt.subscriptions.current_keys(draft, skipped)
    except SourceError as exc:
        raise HTTPException(502, detail={"code": "source_error", "message": str(exc)}) from exc
    except LibraryUnavailable as exc:
        raise HTTPException(409, detail={"code": "library_unavailable", "message": str(exc)}) from exc
    except MetadataError as exc:
        raise metadata_http_error(exc) from exc


@v1.post(
    "/subscriptions",
    response_model=SubscriptionOut,
    status_code=201,
    summary="Create a subscription",
    tags=["subscriptions"],
)
async def post_subscription(rt: RuntimeDep, body: SubscriptionSaveIn) -> SubscriptionOut:
    draft = _library_ready(rt, body.to_draft(require_name=True))
    _searchable(rt, draft)
    _still_valid(rt, draft)
    keys = await _keys_now(rt, draft) if body.skip_existing else []
    sub = rt.subscriptions.store.add(draft)
    if keys:
        rt.subscriptions.store.skip(sub.id, keys)
    return SubscriptionOut.of(sub, rt.subscriptions.store.stats().get(sub.id))


@v1.post("/subscriptions/preview", response_model=PreviewOut, summary="Preview a subscription", tags=["subscriptions"])
@reading
async def post_subscription_preview(
    rt: RuntimeDep, body: SubscriptionIn, subscription: int | None = None
) -> PreviewOut:
    """``subscription``: the saved subscription the draft edits; its skipped episodes show as skipped."""
    skipped = rt.subscriptions.store.skipped(subscription) if subscription is not None else frozenset()
    draft = body.to_draft(require_name=False)
    _searchable(rt, draft)
    try:
        preview = await rt.subscriptions.preview(draft, skipped)
    except SourceError as exc:
        raise HTTPException(502, detail={"code": "source_error", "message": str(exc)}) from exc
    except LibraryUnavailable as exc:
        raise HTTPException(409, detail={"code": "library_unavailable", "message": str(exc)}) from exc
    except MetadataError as exc:
        raise metadata_http_error(exc) from exc
    counts: dict[str, int] = {}
    for item in preview.items:
        counts[item.status] = counts.get(item.status, 0) + 1
    return PreviewOut(
        total=preview.total,
        counts=counts,
        steps=[PreviewStepOut(**dataclasses.asdict(s)) for s in preview.steps],
        items=[
            PreviewItemOut(
                film=FilmOut.of(i.film),
                status=i.status,
                excluded_by=i.excluded_by,
                path=str(i.path) if i.path else None,
                duplicate_of=str(i.duplicate_of) if i.duplicate_of is not None else None,
                download_id=i.download_id,
                results=[
                    ConditionResultOut(
                        passed=r.passed, code=r.code, actual=r.actual, span=list(r.span) if r.span else None
                    )
                    for r in i.results
                ],
                episode=EpisodeOut.of(i.episode) if i.episode else None,
                reason=i.reason,
                in_library=i.in_library,
                special=i.special,
            )
            for i in preview.items
        ],
    )


@v1.get(
    "/subscriptions/{subscription_id}",
    response_model=SubscriptionOut,
    summary="Get a subscription",
    tags=["subscriptions"],
)
async def get_subscription(rt: RuntimeDep, subscription_id: int) -> SubscriptionOut:
    sub = rt.subscriptions.store.get(subscription_id)
    if sub is None:
        raise HTTPException(404, detail={"code": "subscription_not_found"})
    return SubscriptionOut.of(sub, rt.subscriptions.store.stats().get(sub.id))


@v1.put(
    "/subscriptions/{subscription_id}",
    response_model=SubscriptionOut,
    summary="Update a subscription",
    tags=["subscriptions"],
)
async def put_subscription(rt: RuntimeDep, subscription_id: int, body: SubscriptionSaveIn) -> SubscriptionOut:
    draft = _library_ready(rt, body.to_draft(require_name=True))
    _searchable(rt, draft)
    _still_valid(rt, draft)
    keys: list[str] = []
    if body.skip_existing:
        keys = await _keys_now(rt, draft, rt.subscriptions.store.skipped(subscription_id))
    sub = rt.subscriptions.store.update(subscription_id, draft)
    if sub is None:
        raise HTTPException(404, detail={"code": "subscription_not_found"})
    if keys:
        rt.subscriptions.store.skip(sub.id, keys)
    return SubscriptionOut.of(sub, rt.subscriptions.store.stats().get(sub.id))


@v1.delete("/subscriptions/{subscription_id}/skipped", summary="Release skipped episodes", tags=["subscriptions"])
async def delete_subscription_skipped(rt: RuntimeDep, subscription_id: int) -> dict[str, int]:
    """Releases the skipped episodes: the next check loads them like any other match."""
    if rt.subscriptions.store.get(subscription_id) is None:
        raise HTTPException(404, detail={"code": "subscription_not_found"})
    return {"released": rt.subscriptions.store.unskip(subscription_id)}


@v1.delete("/subscriptions/{subscription_id}", status_code=204, summary="Delete a subscription", tags=["subscriptions"])
async def delete_subscription(rt: RuntimeDep, subscription_id: int) -> None:
    """Downloads stay in the history (and on disk); they only lose the link to the subscription."""
    if not rt.subscriptions.store.delete(subscription_id):
        raise HTTPException(404, detail={"code": "subscription_not_found"})


@v1.post("/subscriptions/check", summary="Check all subscriptions", tags=["subscriptions"])
async def post_subscriptions_check(rt: RuntimeDep) -> dict[str, int]:
    """Checks every active subscription now (otherwise this runs after each film list update)."""
    queued = await rt.subscriptions.check_all()
    return {"checked": len(queued), "queued": sum(queued.values())}


@v1.post("/subscriptions/{subscription_id}/check", summary="Check a subscription", tags=["subscriptions"])
async def post_subscription_check(rt: RuntimeDep, subscription_id: int) -> dict[str, int]:
    sub = rt.subscriptions.store.get(subscription_id)
    if sub is None:
        raise HTTPException(404, detail={"code": "subscription_not_found"})
    return {"queued": await rt.subscriptions.check(sub)}


@v1.post(
    "/subscriptions/{subscription_id}/requeue",
    summary="Load failed and cancelled episodes again",
    tags=["subscriptions"],
)
async def post_subscription_requeue(rt: RuntimeDep, subscription_id: int) -> dict[str, int]:
    """Loads the subscription's failed and cancelled episodes again: they leave the history, a check queues them
    with today's naming and quality. ``forgotten``: history entries removed, ``queued``: downloads queued."""
    sub = rt.subscriptions.store.get(subscription_id)
    if sub is None:
        raise HTTPException(404, detail={"code": "subscription_not_found"})
    forgotten, queued = await rt.subscriptions.requeue(sub)
    return {"forgotten": forgotten, "queued": queued}


# -- library, TMDB and Plex -----------------------------------------------------------------------------------


class SecretStateOut(BaseModel):
    configured: bool
    source: Literal["env", "settings"] | None  # env: fixed by the container, the settings page cannot change it


class IntegrationsOut(BaseModel):
    tmdb: SecretStateOut
    plex_token: SecretStateOut


def _secret_state(from_env: str | None, stored: str | None) -> SecretStateOut:
    source: Literal["env", "settings"] | None = "env" if from_env else "settings" if stored else None
    return SecretStateOut(configured=source is not None, source=source)


@v1.get("/integrations", response_model=IntegrationsOut, summary="Integration tokens set", tags=["settings"])
def get_integrations(rt: RuntimeDep) -> IntegrationsOut:
    """Whether the tokens of the library features are set (never their values)."""
    return IntegrationsOut(
        tmdb=_secret_state(rt.settings.tmdb_token, rt.state.secret("tmdb_token")),
        plex_token=_secret_state(rt.settings.plex_token, rt.state.secret("plex_token")),
    )


class SecretsIn(BaseModel):
    """Only the fields sent change; null or "" removes the stored token."""

    tmdb_token: str | None = Field(default=None, max_length=2000)
    plex_token: str | None = Field(default=None, max_length=200)


@v1.put("/integrations/secrets", response_model=IntegrationsOut, summary="Set integration tokens", tags=["settings"])
def put_secrets(rt: RuntimeDep, body: SecretsIn) -> IntegrationsOut:
    for name in body.model_fields_set:
        value = getattr(body, name)
        rt.state.set_secret(name, value.strip() if isinstance(value, str) and value.strip() else None)
    return get_integrations(rt)


@v1.post("/integrations/tmdb/test", summary="Test the TMDB token", tags=["settings"])
async def post_tmdb_test(rt: RuntimeDep) -> dict[str, bool]:
    try:
        await rt.tmdb.test()
    except MetadataError as exc:
        raise metadata_http_error(exc) from exc
    return {"ok": True}


class PlexSectionOut(BaseModel):
    key: str
    title: str
    type: str
    paths: list[str]


class PlexTestOut(BaseModel):
    version: str
    sections: list[PlexSectionOut]


@v1.post("/integrations/plex/test", response_model=PlexTestOut, summary="Test the Plex connection", tags=["settings"])
async def post_plex_test(rt: RuntimeDep) -> PlexTestOut:
    try:
        version = await rt.plex.version()
        sections = await rt.plex.sections()
    except PlexError as exc:
        raise HTTPException(409 if exc.code == "no_url" else 502, detail={"code": f"plex_{exc.code}"}) from exc
    return PlexTestOut(
        version=version,
        sections=[PlexSectionOut(key=s.key, title=s.title, type=s.type, paths=list(s.paths)) for s in sections],
    )


class CandidateOut(BaseModel):
    tmdb_id: int
    name: str  # a movie's title
    original_name: str
    year: int | None
    overview: str


@v1.get("/metadata/series", response_model=list[CandidateOut], summary="Search series on TMDB", tags=["libraries"])
async def get_series_search(rt: RuntimeDep, q: Annotated[str, Query(min_length=1, max_length=200)]) -> list[Any]:
    """Series on TMDB for a name, for assigning a subscription to its series."""
    try:
        found = await rt.tmdb.search(q)
    except MetadataError as exc:
        raise metadata_http_error(exc) from exc
    return [CandidateOut(**dataclasses.asdict(c)) for c in found]


@v1.get("/metadata/movies", response_model=list[CandidateOut], summary="Search movies on TMDB", tags=["libraries"])
async def get_movie_search(
    rt: RuntimeDep,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    year: Annotated[int | None, Query(ge=1870, le=2200)] = None,
) -> list[Any]:
    """Movies on TMDB for a title, for a single download into a movie library."""
    try:
        found = await rt.tmdb.search_movies(q, year)
    except MetadataError as exc:
        raise metadata_http_error(exc) from exc
    return [CandidateOut(**dataclasses.asdict(c)) for c in found]


class MovieOut(BaseModel):
    tmdb_id: int
    title: str
    original_title: str
    year: int | None
    release_date: dt.date | None
    imdb_id: str | None
    runtime: int | None  # minutes
    fetched_at: dt.datetime
    folders: list[str]  # folders of the movie in the library (found by an ID in the name)


@v1.get("/metadata/movies/{tmdb_id}", response_model=MovieOut, summary="Get a movie", tags=["libraries"])
async def get_movie(rt: RuntimeDep, tmdb_id: int, library_id: int | None = None) -> MovieOut:
    """The movie as a download names it; ``library_id``: the movie library to look for its folders in."""
    try:
        movie = await rt.tmdb.movie(tmdb_id)
    except MetadataError as exc:
        raise metadata_http_error(exc) from exc
    library = rt.libraries.get(library_id) if library_id is not None else rt.libraries.only("movies")
    return MovieOut(
        tmdb_id=movie.tmdb_id,
        title=movie.title,
        original_title=movie.original_title,
        year=movie.year,
        release_date=movie.release_date,
        imdb_id=movie.imdb_id,
        runtime=movie.runtime,
        fetched_at=dt.datetime.fromtimestamp(movie.fetched_at, dt.UTC),
        folders=[p.name for p in id_folders(library.path, movie)] if library and library.kind == "movies" else [],
    )


class SeasonOut(BaseModel):
    number: int
    episodes: int
    first_air_date: dt.date | None
    last_air_date: dt.date | None


class SeriesOut(BaseModel):
    tmdb_id: int
    name: str
    original_name: str
    year: int | None
    imdb_id: str | None
    tvdb_id: int | None
    seasons: list[SeasonOut]
    fetched_at: dt.datetime
    folders: list[str]  # folders of the series in the library (found by an ID in the name)


@v1.get("/metadata/series/{tmdb_id}", response_model=SeriesOut, summary="Get a series", tags=["libraries"])
async def get_series(rt: RuntimeDep, tmdb_id: int, refresh: bool = False, library_id: int | None = None) -> SeriesOut:
    """The series as the subscription uses it; ``refresh`` asks TMDB again (new episodes listed there);
    ``library_id``: the series library to look for its folders in (default: the only one)."""
    try:
        series = await rt.tmdb.series(tmdb_id, max_age=0 if refresh else None)
    except MetadataError as exc:
        raise metadata_http_error(exc) from exc
    seasons = []
    for number in series.seasons:
        episodes = [e for e in series.episodes if e.season == number]
        days = sorted(e.air_date for e in episodes if e.air_date)
        seasons.append(
            SeasonOut(
                number=number,
                episodes=len(episodes),
                first_air_date=days[0] if days else None,
                last_air_date=days[-1] if days else None,
            )
        )
    library = rt.libraries.get(library_id) if library_id is not None else rt.libraries.only("series")
    return SeriesOut(
        tmdb_id=series.tmdb_id,
        name=series.name,
        original_name=series.original_name,
        year=series.year,
        imdb_id=series.imdb_id,
        tvdb_id=series.tvdb_id,
        seasons=seasons,
        fetched_at=dt.datetime.fromtimestamp(series.fetched_at, dt.UTC),
        folders=[p.name for p in id_folders(library.path, series)] if library and library.kind == "series" else [],
    )


# An example for the template field of the settings page: a regular episode and a special, real TMDB data.
SAMPLE_SERIES = SeriesInfo(
    tmdb_id=73860,
    name="Die Anstalt",
    original_name="Die Anstalt",
    first_air_date=dt.date(2014, 2, 4),
    imdb_id="tt4277128",
    tvdb_id=279577,
    episodes=(
        EpisodeInfo(13, 8, "Die Anstalt - Jetzt erst rechts!", dt.date(2026, 10, 6), 43),
        EpisodeInfo(0, 1, "Spezialauftrag (1): Die Prüf-Anstalt", dt.date(2026, 1, 30), 30),
    ),
)
SAMPLE_FILM = Film(
    id=0,
    channel="ZDF",
    topic="Die Anstalt",
    title="Die Anstalt – Jetzt erst rechts! - Politsatire mit Max Uthoff, Maike Kühl und Claus von Wagner (S2026/E08)",
    description="",
    timestamp=1791317700,
    duration=2520,
    size=None,
    url=None,
    url_low=None,
    url_hd=None,
    url_subtitle=None,
    url_website=None,
    geo=(),
)


# A movie for the template field of a movie library: real TMDB data and a real film list entry (3sat, 11.10.2026).
SAMPLE_MOVIE = MovieInfo(
    tmdb_id=9322,
    title="Nikita",
    original_title="Nikita",
    release_date=dt.date(1990, 2, 21),
    imdb_id="tt0100263",
    runtime=118,
)
SAMPLE_MOVIE_FILM = dataclasses.replace(
    SAMPLE_FILM,
    channel="3Sat",
    topic="Spielfilm",
    title="Nikita - Spielfilm, Frankreich/Italien 1990",
    timestamp=1791753360,
    duration=6629,
)


class LibraryPreviewIn(BaseModel):
    kind: Kind = "series"
    template: str = Field(max_length=500)
    normalize: bool = False


@v1.post("/libraries/preview", summary="Preview the paths of a library", tags=["libraries"])
@reading
def post_library_preview(rt: RuntimeDep, body: LibraryPreviewIn) -> dict[str, list[str]]:
    """Paths under a library template, relative to the library: a regular episode and a special, or a movie. The
    live example of the library form."""
    _check_template(body.kind, body.template)
    root = Path("/library")
    if body.kind == "movies":
        movie = movie_path(
            SAMPLE_MOVIE_FILM,
            SAMPLE_MOVIE,
            body.template,
            root=root,
            existing=None,
            normalize=body.normalize,
            timezone=rt.settings.timezone,
        )
        return {"paths": [str(movie.relative_to(root))]}
    paths = [
        library_path(
            SAMPLE_FILM,
            episode,
            SAMPLE_SERIES,
            body.template,
            root=root,
            existing=None,
            normalize=body.normalize,
            timezone=rt.settings.timezone,
        )
        for episode in SAMPLE_SERIES.episodes
    ]
    return {"paths": [str(p.relative_to(root)) for p in paths]}


def _check_template(kind: Kind, template: str) -> None:
    if not template.strip():
        raise HTTPException(422, detail={"code": "template_empty"})
    if bad := unknown_library_placeholders(template, kind):
        raise HTTPException(422, detail={"code": "unknown_placeholders", "placeholders": bad})


class LibraryIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: Kind
    path: str = Field(max_length=1000, pattern=r"^/")  # inside the container, a mount of its own
    template: str = Field(max_length=500)
    normalize: bool = False  # simplify names as in the download folder; off: names as on TMDB (Plex, FileBot)
    plex_path: str | None = Field(default=None, max_length=1000, pattern=r"^/")  # the folder as Plex sees it
    plex_refresh: bool = False  # Plex reads the folder again after each download into it

    def to_draft(self) -> LibraryDraft:
        return LibraryDraft(
            name=self.name.strip(),
            kind=self.kind,
            path=Path(os.path.normpath(self.path)),
            template=self.template.strip(),
            normalize=self.normalize,
            plex_path=os.path.normpath(self.plex_path) if self.plex_path and self.plex_path.strip() else None,
            plex_refresh=self.plex_refresh,
        )


class LibraryOut(LibraryIn):
    id: int
    problem: str | None  # the last write test: missing (not mounted?), not mounted, not writable
    subscriptions: list[int]  # subscriptions that load into the library

    @classmethod
    def of(cls, rt: Runtime, library: Library) -> "LibraryOut":
        return cls(
            id=library.id,
            name=library.name,
            kind=library.kind,
            path=str(library.path),
            template=library.template,
            normalize=library.normalize,
            plex_path=library.plex_path,
            plex_refresh=library.plex_refresh,
            problem=rt.library_problems.get(library.id),
            subscriptions=rt.libraries.subscriptions(library.id),
        )


@v1.get("/libraries", response_model=list[LibraryOut], summary="List libraries", tags=["libraries"])
def get_libraries(rt: RuntimeDep) -> list[LibraryOut]:
    return [LibraryOut.of(rt, library) for library in rt.libraries.entries()]


def _library_draft(rt: Runtime, body: LibraryIn, current: Library | None = None) -> LibraryDraft:
    """Checks a library before it is saved: a template the kind knows, a folder of its own (not the root, not the
    configuration, no other library inside or around it) and, when the folder is new, a write test."""
    draft = body.to_draft()
    _check_template(draft.kind, draft.template)
    if not draft.name:
        raise HTTPException(422, detail={"code": "name_missing"})
    config = rt.settings.config_dir
    if len(draft.path.parts) < 2 or draft.path.is_relative_to(config) or config.is_relative_to(draft.path):
        raise HTTPException(422, detail={"code": "library_path_invalid"})
    for other in rt.libraries.entries():
        if current is not None and other.id == current.id:
            continue
        if other.name.casefold() == draft.name.casefold():
            raise HTTPException(409, detail={"code": "library_name_taken"})
        if draft.path.is_relative_to(other.path) or other.path.is_relative_to(draft.path):
            raise HTTPException(409, detail={"code": "library_path_overlap", "library": other.name})
    moved = current is None or draft.path != current.path
    if moved and (problem := write_test(draft.path, create=False, check_mount=rt.settings.check_mounts)):
        raise HTTPException(422, detail={"code": "library_not_writable", "message": problem})
    if current is not None and draft.kind != current.kind and (subs := rt.libraries.subscriptions(current.id)):
        raise HTTPException(409, detail={"code": "library_in_use", "subscriptions": subs, "downloads": 0})
    return draft


@v1.post("/libraries", response_model=LibraryOut, status_code=201, summary="Create a library", tags=["libraries"])
def post_library(rt: RuntimeDep, body: LibraryIn) -> LibraryOut:
    library = rt.libraries.add(_library_draft(rt, body))
    rt.check_directories()
    return LibraryOut.of(rt, library)


@v1.put("/libraries/{library_id}", response_model=LibraryOut, summary="Update a library", tags=["libraries"])
def put_library(rt: RuntimeDep, library_id: int, body: LibraryIn) -> LibraryOut:
    """A new path moves the library: downloads in it keep their place relative to it (move the files along)."""
    current = rt.libraries.get(library_id)
    if current is None:
        raise HTTPException(404, detail={"code": "library_not_found"})
    library = rt.libraries.update(library_id, _library_draft(rt, body, current))
    assert library is not None
    rt.check_directories()
    return LibraryOut.of(rt, library)


@v1.delete("/libraries/{library_id}", status_code=204, summary="Delete a library", tags=["libraries"])
def delete_library(rt: RuntimeDep, library_id: int) -> None:
    """Files stay where they are. Refused while subscriptions or unfinished downloads use the library."""
    try:
        deleted = rt.libraries.delete(library_id)
    except LibraryInUse as exc:
        detail = {"code": "library_in_use", "subscriptions": exc.subscriptions, "downloads": exc.downloads}
        raise HTTPException(409, detail=detail) from exc
    if not deleted:
        raise HTTPException(404, detail={"code": "library_not_found"})
    rt.check_directories()


# -- API keys ------------------------------------------------------------------------------------------------


class ApiKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scope: auth.KeyScope = "read"  # read: GET and the previews; write: everything


class ApiKeyOut(BaseModel):
    id: int
    name: str
    prefix: str  # the start of the key, to tell keys apart
    scope: auth.KeyScope
    created_at: dt.datetime
    last_used_at: dt.datetime | None

    @classmethod
    def of(cls, key: auth.ApiKey) -> "ApiKeyOut":
        used = dt.datetime.fromtimestamp(key.last_used_at, dt.UTC) if key.last_used_at else None
        created = dt.datetime.fromtimestamp(key.created_at, dt.UTC)
        return cls(id=key.id, name=key.name, prefix=key.prefix, scope=key.scope, created_at=created, last_used_at=used)


class ApiKeyCreatedOut(ApiKeyOut):
    key: str  # shown this once; mtdl-web keeps only its hash


@v1.get("/api-keys", response_model=list[ApiKeyOut], summary="List API keys", tags=["access"])
def get_api_keys(rt: RuntimeDep) -> list[ApiKeyOut]:
    return [ApiKeyOut.of(k) for k in rt.access.api_keys()]


@v1.post("/api-keys", response_model=ApiKeyCreatedOut, status_code=201, summary="Create an API key", tags=["access"])
def post_api_key(rt: RuntimeDep, body: ApiKeyIn) -> ApiKeyCreatedOut:
    """A key for scripts, other services and assistants (MCP): header X-Api-Key, in every access mode."""
    key, plain = rt.access.create_key(body.name, body.scope)
    return ApiKeyCreatedOut(**ApiKeyOut.of(key).model_dump(), key=plain)


@v1.delete("/api-keys/{key_id}", status_code=204, summary="Delete an API key", tags=["access"])
def delete_api_key(rt: RuntimeDep, key_id: int) -> None:
    if not rt.access.delete_key(key_id):
        raise HTTPException(404, detail={"code": "api_key_not_found"})


# -- takeover from MediathekView ------------------------------------------------------------------------------------


class TakeoverIn(BaseModel):
    history: str = Field(default="", max_length=20_000_000)  # history.txt
    abo_history: str = Field(default="", max_length=20_000_000)  # downloadAbos.txt
    settings: str = Field(default="", max_length=20_000_000)  # mediathek.xml
    apply: bool = False  # False: dry run, nothing changes


@v1.post("/takeover/mediathekview", summary="Take over from MediathekView", tags=["settings"])
async def post_takeover(rt: RuntimeDep, body: TakeoverIn) -> dict[str, Any]:
    """Download history and subscriptions of the MediathekView desktop app: first as a dry run, then with
    ``apply``. Downloads count as done (subscriptions skip them), subscriptions arrive paused."""

    def run() -> takeover.Report:
        report = takeover.plan(
            body.history,
            body.abo_history,
            body.settings,
            films=rt.films,
            downloads=rt.downloads,
            subscriptions=rt.subscriptions.store,
        )
        if body.apply:
            report = takeover.apply(
                report, downloads=rt.downloads, subscriptions=rt.subscriptions.store, timezone=rt.settings.timezone
            )
        return report

    try:
        report = await asyncio.to_thread(run)  # url lookup scans the film list
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "unreadable", "message": str(exc)}) from exc
    counts: dict[str, int] = {}
    items = []
    for item in report.items:
        counts[item.status] = counts.get(item.status, 0) + 1
        counts[f"by_{item.found.how}"] = counts.get(f"by_{item.found.how}", 0) + 1
        first = item.found.lines[0]
        days = [line.day for line in item.found.lines if line.day]
        items.append(
            {
                "lines": [line.number for line in item.found.lines],
                "day": max(days).isoformat() if days else None,
                "topic": first.topic,
                "title": first.title,
                "url": first.url,
                "how": item.found.how,
                "status": item.status,
                "film": FilmOut.of(item.found.film).model_dump() if item.found.film else None,
                "repeats": max(0, len(item.found.films) - 1),  # same episode on other days, taken over as well
                "subscription": item.subscription,
            }
        )
    subs = []
    for converted in report.subscriptions:
        draft = converted.draft
        subs.append(
            {
                "name": draft.name if draft else converted.old.name,
                "query": draft.query if draft else "",
                "conditions": [condition_to_json(c) for c in draft.conditions] if draft else [],
                "folder": draft.naming.folder if draft else None,
                "include_description": draft.include_description if draft else False,
                "active_before": converted.old.active,
                "notes": list(converted.notes),
                "error": converted.error,
                "exists": bool(draft and draft.name in report.existing_names),
            }
        )
    return {
        "lines": report.lines,
        "counts": counts,
        "items": items,
        "subscriptions": subs,
        "applied": report.applied,
        "imported_downloads": report.imported_downloads,
        "imported_subscriptions": report.imported_subscriptions,
    }


# -- webhooks ----------------------------------------------------------------------------------------------------


class WebhookIn(BaseModel):
    url: str = Field(pattern=r"^https?://\S+$", max_length=2000)
    events: list[EventName] = Field(min_length=1)
    enabled: bool = True
    secret: str | None = Field(
        default=None, max_length=200, description="Signs the body. On update: null keeps the stored one, '' removes it."
    )
    headers: dict[str, str | None] | None = Field(
        default=None,
        description="Own headers, e.g. an API key. On update: null keeps all stored ones, an object replaces them;"
        " a header with the value null keeps its stored value.",
    )
    body_template: str | None = Field(
        default=None,
        max_length=MAX_TEMPLATE,
        description="Replaces the default JSON body. Placeholders: {{event}}, {{time}}, {{data.<field>}}.",
    )

    def config(self, current: WebhookConfig | None = None) -> WebhookConfig:
        """The configuration to store, with secret and headers kept where the request leaves them out."""
        keep = current or WebhookConfig(url="", events=())
        headers = keep.headers
        if self.headers is not None:
            stored = {name.lower(): value for name, value in keep.headers}
            unknown = [name for name, value in self.headers.items() if value is None and name.lower() not in stored]
            if unknown:
                raise HTTPException(422, detail={"code": "invalid_headers", "headers": unknown})
            headers = tuple(  # a pasted key often ends in a space or tab
                (name.strip(), stored[name.lower()] if value is None else value.strip(" \t"))
                for name, value in self.headers.items()
            )
        config = WebhookConfig(
            url=self.url,
            events=tuple(self.events),
            enabled=self.enabled,
            secret=keep.secret if self.secret is None else self.secret or None,
            headers=headers,
            body_template=self.body_template or None,
        )
        detail = webhook_problem(config)
        if detail:
            raise HTTPException(422, detail=detail)
        return config


class WebhookOut(BaseModel):
    id: int
    url: str
    events: list[str]
    enabled: bool
    has_secret: bool  # the secret itself never leaves the server
    header_names: list[str]  # nor do header values (API keys)
    body_template: str | None
    created_at: dt.datetime
    last_status: int | None
    last_error: str | None
    last_event: str | None
    last_sent_at: dt.datetime | None

    @classmethod
    def of(cls, hook: Webhook) -> "WebhookOut":
        def when(value: int | None) -> dt.datetime | None:
            return None if value is None else dt.datetime.fromtimestamp(value, dt.UTC)

        config = hook.config
        return cls(
            id=hook.id,
            url=config.url,
            events=list(config.events),
            enabled=config.enabled,
            has_secret=bool(config.secret),
            header_names=[name for name, _ in config.headers],
            body_template=config.body_template,
            created_at=dt.datetime.fromtimestamp(hook.created_at, dt.UTC),
            last_status=hook.last_status,
            last_error=hook.last_error,
            last_event=hook.last_event,
            last_sent_at=when(hook.last_sent_at),
        )


@v1.get("/webhooks", response_model=list[WebhookOut], summary="List webhooks", tags=["webhooks"])
async def get_webhooks(rt: RuntimeDep) -> list[WebhookOut]:
    return [WebhookOut.of(h) for h in rt.webhooks.store.entries()]


@v1.post("/webhooks", response_model=WebhookOut, status_code=201, summary="Create a webhook", tags=["webhooks"])
async def post_webhook(rt: RuntimeDep, body: WebhookIn) -> WebhookOut:
    return WebhookOut.of(rt.webhooks.store.add(body.config()))


@v1.put("/webhooks/{webhook_id}", response_model=WebhookOut, summary="Update a webhook", tags=["webhooks"])
async def put_webhook(rt: RuntimeDep, webhook_id: int, body: WebhookIn) -> WebhookOut:
    current = rt.webhooks.store.get(webhook_id)
    hook = None if current is None else rt.webhooks.store.update(webhook_id, body.config(current.config))
    if hook is None:
        raise HTTPException(404, detail={"code": "webhook_not_found"})
    return WebhookOut.of(hook)


@v1.delete("/webhooks/{webhook_id}", status_code=204, summary="Delete a webhook", tags=["webhooks"])
async def delete_webhook(rt: RuntimeDep, webhook_id: int) -> None:
    if not rt.webhooks.store.delete(webhook_id):
        raise HTTPException(404, detail={"code": "webhook_not_found"})


@v1.post("/webhooks/{webhook_id}/test", summary="Send a test event", tags=["webhooks"])
async def post_webhook_test(rt: RuntimeDep, webhook_id: int) -> dict[str, Any]:
    """Sends a ``webhook.test`` event once and answers with the result."""
    hook = rt.webhooks.store.get(webhook_id)
    if hook is None:
        raise HTTPException(404, detail={"code": "webhook_not_found"})
    delivery = await rt.webhooks.test(hook)
    return {"ok": delivery.ok, "status": delivery.status, "error": delivery.error}


# -- channel logos (optional) -----------------------------------------------------------------------------------------

# Served from our own origin: an SVG could carry script, so it gets a sandbox even when opened directly.
LOGO_HEADERS = {
    "Cache-Control": "public, max-age=604800",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
}


@v1.get(
    "/logos/{channel}",
    response_class=Response,
    responses={200: {"content": {"image/svg+xml": {}}}},
    summary="Channel logo",
    tags=["system"],
)
async def get_logo(rt: RuntimeDep, channel: str) -> Response:
    """The channel's logo when the setting ``channel_logos`` is on and the MediathekView project has one; 404 otherwise
    (the UI shows the name instead)."""
    if not rt.state.settings().channel_logos:
        raise HTTPException(404, detail={"code": "logos_off"})
    data = await rt.logos.get(channel)
    if data is None:
        raise HTTPException(404, detail={"code": "no_logo"})
    return Response(data, media_type="image/svg+xml", headers=LOGO_HEADERS)


@v1.get(
    "/attribution/{source}",
    response_class=Response,
    responses={200: {"content": {"image/svg+xml": {}}}},
    summary="Logo of a data source",
    tags=["system"],
)
async def get_attribution(rt: RuntimeDep, source: Literal["tmdb"]) -> Response:
    """The logo a data source asks to show with its notice (TMDB), served from here: the browser loads nothing
    from elsewhere."""
    data = await rt.logos.attribution(source)
    if data is None:
        raise HTTPException(404, detail={"code": "no_logo"})
    return Response(data, media_type="image/svg+xml", headers=LOGO_HEADERS)


# -- hook scripts ----------------------------------------------------------------------------------------


class HookRunOut(BaseModel):
    started_at: dt.datetime
    seconds: float
    exit_code: int | None  # None: did not start or stopped after the timeout, see error
    output: str  # the end of stdout and stderr
    error: str | None


class HookScriptOut(BaseModel):
    event: str
    name: str
    executable: bool  # without the executable bit a script is switched off
    last_run: HookRunOut | None  # since the start of mtdl-web


class HooksOut(BaseModel):
    directory: str
    timeout: float
    scripts: list[HookScriptOut]


@v1.get("/hooks", response_model=HooksOut, summary="List hook scripts", tags=["settings"])
async def get_hooks(rt: RuntimeDep) -> HooksOut:
    """Scripts in the hooks folder (``<event>.d/``) and their last run. Read only: scripts come from the config
    folder alone, the API neither writes nor starts one."""
    runner = rt.hooks
    out = []
    for script in await asyncio.to_thread(runner.scripts):
        run = runner.last.get((script.event, script.name))
        last = None if run is None else HookRunOut(**{f: getattr(run, f) for f in HookRunOut.model_fields})
        out.append(HookScriptOut(event=script.event, name=script.name, executable=script.executable, last_run=last))
    return HooksOut(directory=str(runner.directory), timeout=runner.timeout, scripts=out)


# -- status for the shell (polled) -------------------------------------------------------------------------------------


class RunningOut(BaseModel):
    id: int
    channel: str
    topic: str
    title: str
    done: int
    total: int | None
    speed: float
    phase: str


class DownloadSummaryOut(BaseModel):
    counts: dict[str, int]
    running: list[RunningOut]
    speed: float  # byte/s of all running downloads
    progress: float | None  # 0..1 over the running downloads with a known size


class LimitOut(BaseModel):
    bandwidth_limit: int  # the normal limit and its switch
    bandwidth_enabled: bool
    speed_unit: str
    rate: int  # in effect now, byte/s, 0 = unlimited
    source: Literal["normal", "schedule", "paused"]  # paused: the switch opened a schedule window early
    until: dt.datetime | None  # next change by the schedule

    @classmethod
    def of(cls, rt: Runtime, settings: AppSettings) -> "LimitOut":
        state = rt.limit_state(settings)
        return cls(
            bandwidth_limit=settings.bandwidth_limit,
            bandwidth_enabled=settings.bandwidth_enabled,
            speed_unit=settings.speed_unit,
            rate=state.rate,
            source=state.source,
            until=state.until,
        )


class PauseIn(BaseModel):
    paused: bool


@v1.put("/limit/pause", response_model=LimitOut, summary="Pause the scheduled bandwidth limit", tags=["downloads"])
async def put_limit_pause(rt: RuntimeDep, body: PauseIn) -> LimitOut:
    """Inside a schedule window: ``paused`` opens the limit until the window ends; false returns to the schedule."""
    rt.pause_schedule(body.paused)
    return LimitOut.of(rt, rt.state.settings())


class StatusOut(BaseModel):
    version: str
    filmlist: FilmlistOut
    downloads: DownloadSummaryOut
    limit: LimitOut
    subscriptions: int
    writable: dict[str, str | None]
    problems: list[str]


@v1.get("/status", response_model=StatusOut, summary="Current status", tags=["system"])
async def get_status(rt: RuntimeDep) -> StatusOut:
    """Everything the status bar shows, in one request (the UI polls it every few seconds)."""
    running = []
    total = done = 0
    for download in rt.downloads.queue.entries(("running",), limit=50):
        live = rt.downloads.live.get(download.id)
        film = download.film
        current = RunningOut(
            id=download.id,
            channel=film.channel,
            topic=film.topic,
            title=film.title,
            done=live.done if live else download.bytes_done,
            total=live.total if live else download.bytes_total,
            speed=live.speed if live else 0.0,
            phase=live.phase if live else "download",
        )
        running.append(current)
        if current.total:
            total += current.total
            done += min(current.done, current.total)
    settings = rt.state.settings()
    problems = [f"{name} not writable: {problem}" for name, problem in rt.writable.items() if problem]
    return StatusOut(
        version=__version__,
        filmlist=get_filmlist(rt),
        downloads=DownloadSummaryOut(
            counts=rt.downloads.queue.counts(),
            running=running,
            speed=sum(r.speed for r in running),
            progress=done / total if total else None,
        ),
        limit=LimitOut.of(rt, settings),
        subscriptions=len(rt.subscriptions.store.entries()),
        writable=rt.writable,
        problems=problems,
    )
