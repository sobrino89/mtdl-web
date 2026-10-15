"""Settings from the environment. Everything the user changes at runtime lives in the state database instead."""

import ipaddress
import os
import zoneinfo
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, get_args

from mtdl_web.mounts import in_container

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _flag(environ: Mapping[str, str], name: str, default: bool) -> bool:
    value = environ.get(name, "").strip().lower()
    if not value:
        return default
    if value in _TRUE | _FALSE:
        return value in _TRUE
    raise ValueError(f"{name} must be one of {sorted(_TRUE | _FALSE)}, got {value!r}")


def _positive(environ: Mapping[str, str], name: str, default: int) -> int:
    value = environ.get(name, "").strip()
    if not value:
        return default
    if not value.isdigit() or int(value) < 1:
        raise ValueError(f"{name} must be a whole number of at least 1, got {value!r}")
    return int(value)


def _timezone(environ: Mapping[str, str]) -> zoneinfo.ZoneInfo:
    name = environ.get("TZ", "").strip() or "Europe/Berlin"
    try:
        return zoneinfo.ZoneInfo(name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"TZ is not a known time zone: {name!r}") from exc


def available_cpus() -> int:
    return os.process_cpu_count() or 1


# Access (auth.py): password (default), local, proxy, none.
AuthMode = Literal["password", "local", "proxy", "none"]
Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def _auth_mode(environ: Mapping[str, str]) -> AuthMode:
    value = environ.get("MTDL_AUTH", "").strip().lower() or "password"
    for mode in get_args(AuthMode):
        if value == mode:
            return mode  # type: ignore[no-any-return]
    raise ValueError(f"MTDL_AUTH must be one of {list(get_args(AuthMode))}, got {value!r}")


def _password(environ: Mapping[str, str]) -> str | None:
    """MTDL_PASSWORD, or the content of the file MTDL_PASSWORD_FILE names (Docker secrets)."""
    if path := environ.get("MTDL_PASSWORD_FILE", "").strip():
        try:
            return Path(path).read_text(encoding="utf-8").strip("\r\n") or None
        except OSError as exc:
            raise ValueError(f"MTDL_PASSWORD_FILE cannot be read: {exc.strerror or exc}") from exc
    return environ.get("MTDL_PASSWORD", "") or None


def _networks(environ: Mapping[str, str], name: str) -> tuple[Network, ...]:
    out = []
    for part in environ.get(name, "").replace(";", ",").split(","):
        if part.strip():
            try:
                out.append(ipaddress.ip_network(part.strip(), strict=False))
            except ValueError as exc:
                raise ValueError(f"{name}: not an address or network: {part.strip()!r}") from exc
    return tuple(out)


@dataclass(frozen=True, slots=True)
class Settings:
    # State: subscriptions, history, settings changed in the UI (back it up).
    config_dir: Path = Path("/config")
    # Film list and its index: rebuilt from the server at any time (leave it out of backups).
    cache_dir: Path = Path("/config/cache")
    # Default target for downloads and subscriptions.
    download_dir: Path = Path("/downloads")
    port: int = 8080
    log_level: str = "info"
    # Day boundaries for date conditions and the display of times.
    timezone: zoneinfo.ZoneInfo = field(default_factory=lambda: zoneinfo.ZoneInfo("Europe/Berlin"))
    # Index film descriptions for full-text search. Off makes the index smaller and hides the search option.
    index_description: bool = True
    # Upper bound for processes doing CPU work, 1 = no parallelism. The film list import uses at most three (one
    # writer, two parsers: SQLite's single writer is the bottleneck); ffmpeg and subtitle conversion will use all.
    workers: int = field(default_factory=available_cpus)
    # Seconds a hook script may run before its process group is stopped.
    hook_timeout: int = 600
    # Series library: subscriptions may load straight into it, named after TMDB. Unset: the option is off.
    shows_dir: Path | None = None
    # Access tokens; set here they win over the settings page (which then shows them as fixed). Never logged.
    tmdb_token: str | None = field(default=None, repr=False)
    plex_token: str | None = field(default=None, repr=False)
    # Refuse folders that are not mounted (mounts.py). from_env turns it on inside containers (MTDL_CHECK_MOUNTS,
    # default on); constructed directly (tests, tools) it is off.
    check_mounts: bool = False
    # Access (auth.py): who may use the UI and the API. "password" unless MTDL_AUTH says otherwise.
    auth: AuthMode = "password"
    # Fixed password (MTDL_PASSWORD or MTDL_PASSWORD_FILE); unset, the first visit sets one (with a setup code).
    password: str | None = field(default=None, repr=False)
    # MTDL_PASSWORD_RESET: forget the stored password at this start, once per setting (Access.reset_on_start).
    password_reset: bool = False
    # Mode "proxy": the header a signing-in reverse proxy sets, and the only addresses allowed to set it.
    auth_header: str = "Remote-User"
    trusted_proxies: tuple[Network, ...] = ()

    @property
    def hooks_dir(self) -> Path:
        return self.config_dir / "hooks"

    @property
    def state_db(self) -> Path:
        return self.config_dir / "mtdl-web.sqlite"

    @property
    def film_db(self) -> Path:
        return self.cache_dir / "films.sqlite"

    @property
    def list_dir(self) -> Path:
        return self.cache_dir / "filmliste"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] = os.environ) -> "Settings":
        config_dir = Path(environ.get("MTDL_CONFIG_DIR", "").strip() or "/config")
        auth = _auth_mode(environ)
        proxies = _networks(environ, "MTDL_TRUSTED_PROXIES")
        if auth == "proxy" and not proxies:
            raise ValueError("MTDL_AUTH=proxy needs MTDL_TRUSTED_PROXIES: the addresses of the signing-in proxy")
        return cls(
            config_dir=config_dir,
            cache_dir=Path(environ.get("MTDL_CACHE_DIR", "").strip() or config_dir / "cache"),
            download_dir=Path(environ.get("MTDL_DOWNLOAD_DIR", "").strip() or "/downloads"),
            port=_positive(environ, "MTDL_PORT", 8080),
            log_level=environ.get("MTDL_LOG_LEVEL", "").strip().lower() or "info",
            timezone=_timezone(environ),
            index_description=_flag(environ, "MTDL_INDEX_DESCRIPTION", True),
            workers=_positive(environ, "MTDL_WORKERS", available_cpus()),
            hook_timeout=_positive(environ, "MTDL_HOOK_TIMEOUT", 600),
            shows_dir=Path(shows) if (shows := environ.get("MTDL_SHOWS_DIR", "").strip()) else None,
            tmdb_token=environ.get("MTDL_TMDB_TOKEN", "").strip() or None,
            plex_token=environ.get("MTDL_PLEX_TOKEN", "").strip() or None,
            check_mounts=_flag(environ, "MTDL_CHECK_MOUNTS", True) and in_container(),
            auth=auth,
            password=_password(environ),
            password_reset=_flag(environ, "MTDL_PASSWORD_RESET", False),
            auth_header=environ.get("MTDL_AUTH_HEADER", "").strip() or "Remote-User",
            trusted_proxies=proxies,
        )
