"""Application state in /config (back it up): settings changed in the UI, downloads, later subscriptions."""

import json
import secrets
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mtdl_web.db import Database
from mtdl_web.library import DEFAULT_TEMPLATE
from mtdl_web.library import unknown_placeholders as unknown_library_placeholders
from mtdl_web.naming import unknown_placeholders

MIGRATIONS = (
    """
    CREATE TABLE setting (key TEXT PRIMARY KEY, value TEXT NOT NULL) STRICT;
    """,
    """
    CREATE TABLE download (
        id INTEGER PRIMARY KEY,
        film_id INTEGER NOT NULL,
        film TEXT NOT NULL,
        subscription_id INTEGER,
        episode_key TEXT NOT NULL,
        quality TEXT NOT NULL,
        subtitles INTEGER NOT NULL,
        path TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('queued', 'running', 'done', 'failed', 'cancelled')),
        bytes_done INTEGER NOT NULL DEFAULT 0,
        bytes_total INTEGER,
        error TEXT,
        attempts INTEGER NOT NULL DEFAULT 0,
        not_before INTEGER,
        created_at INTEGER NOT NULL,
        started_at INTEGER,
        finished_at INTEGER
    ) STRICT;
    CREATE INDEX download_state ON download (state, not_before, id);
    CREATE INDEX download_episode ON download (episode_key);
    CREATE INDEX download_subscription ON download (subscription_id);
    """,
    """
    CREATE TABLE subscription (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        query TEXT NOT NULL,
        include_description INTEGER NOT NULL DEFAULT 0,
        source TEXT,
        conditions TEXT NOT NULL,
        naming TEXT NOT NULL,
        quality TEXT,
        subtitles INTEGER,
        state TEXT NOT NULL CHECK (state IN ('active', 'paused')),
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        last_check INTEGER,
        last_error TEXT,
        last_queued INTEGER
    ) STRICT;
    """,
    # Queue order the user can change, and the kind of a failure for the explanation in the UI.
    """
    ALTER TABLE download ADD COLUMN position INTEGER;
    UPDATE download SET position = id;
    ALTER TABLE download ADD COLUMN error_code TEXT;
    CREATE INDEX download_queue ON download (state, position);
    """,
    # Episodes a subscription leaves alone: what matched when it was saved with "only future episodes".
    """
    CREATE TABLE subscription_skip (
        subscription_id INTEGER NOT NULL,
        episode_key TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        PRIMARY KEY (subscription_id, episode_key)
    ) STRICT, WITHOUT ROWID;
    """,
    # Webhooks: events as JSON POST requests, optionally signed with a secret.
    """
    CREATE TABLE webhook (
        id INTEGER PRIMARY KEY,
        url TEXT NOT NULL,
        events TEXT NOT NULL,
        secret TEXT,
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at INTEGER NOT NULL,
        last_status INTEGER,
        last_error TEXT,
        last_event TEXT,
        last_sent_at INTEGER
    ) STRICT;
    """,
    # Webhooks for receivers with their own format: own headers (JSON list of [name, value]) and a body template.
    """
    ALTER TABLE webhook ADD COLUMN headers TEXT NOT NULL DEFAULT '[]';
    ALTER TABLE webhook ADD COLUMN body_template TEXT;
    """,
    # TMDB answers (metadata.py): a series with every episode, asked again after six hours.
    """
    CREATE TABLE metadata_cache (key TEXT PRIMARY KEY, fetched_at INTEGER NOT NULL, value TEXT NOT NULL) STRICT;
    """,
    # Libraries (libraries.py): series and movie folders; a download into one keeps its path relative to it.
    """
    CREATE TABLE library (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('series', 'movies')),
        path TEXT NOT NULL UNIQUE,
        template TEXT NOT NULL,
        normalize INTEGER NOT NULL DEFAULT 0,
        plex_path TEXT,
        plex_refresh INTEGER NOT NULL DEFAULT 0,
        position INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL
    ) STRICT;
    ALTER TABLE download ADD COLUMN library_id INTEGER;
    """,
    # Access (auth.py): browser sessions and API keys, both only as SHA-256 hashes of their secret.
    """
    CREATE TABLE session (
        token_hash TEXT PRIMARY KEY,
        created_at INTEGER NOT NULL,
        last_seen INTEGER NOT NULL,
        user_agent TEXT NOT NULL DEFAULT '',
        address TEXT NOT NULL DEFAULT ''
    ) STRICT, WITHOUT ROWID;
    CREATE TABLE api_key (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        key_hash TEXT NOT NULL UNIQUE,
        prefix TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        last_used_at INTEGER
    ) STRICT;
    """,
    # A subscription valid until a day (ISO date); afterwards it pauses itself or goes.
    """
    ALTER TABLE subscription ADD COLUMN valid_until TEXT;
    ALTER TABLE subscription ADD COLUMN on_expiry TEXT NOT NULL DEFAULT 'pause'
        CHECK (on_expiry IN ('pause', 'delete'));
    """,
    # What an API key may do: read, or write as well. Older keys could do everything.
    """
    ALTER TABLE api_key ADD COLUMN scope TEXT NOT NULL DEFAULT 'write' CHECK (scope IN ('read', 'write'));
    """,
    # Special versions per subscription: as in the settings, always out, or loaded too.
    """
    ALTER TABLE subscription ADD COLUMN special_versions TEXT NOT NULL DEFAULT 'setting'
        CHECK (special_versions IN ('setting', 'exclude', 'include'));
    """,
)


class AppSettings(BaseModel):
    """Settings the user changes at runtime. New fields need a default: stored documents may lack them."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    # Global bandwidth limit for every download byte in byte/s, 0 = unlimited. The switch keeps the value.
    bandwidth_limit: int = Field(default=0, ge=0)
    bandwidth_enabled: bool = True
    # Unit for entering the limit and for all speed displays (decimal: 1 MB/s = 8 Mbit/s).
    speed_unit: Literal["Mbit/s", "MB/s"] = "Mbit/s"
    # Limit schedule (schedule.py): on these days (0 = Monday) from start to end the schedule's limit applies,
    # otherwise the one above. Default: Monday to Friday, 8 to 18, 20 Mbit/s.
    schedule_enabled: bool = False
    schedule_days: list[Annotated[int, Field(ge=0, le=6)]] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    schedule_start: str = Field(default="08:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    schedule_end: str = Field(default="18:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    schedule_limit: int = Field(default=2_500_000, ge=0)  # byte/s, 0 = unlimited
    parallel_downloads: int = Field(default=2, ge=1, le=10)
    # Preferred quality; falls back to the next lower one that exists.
    quality: Literal["hd", "normal", "low"] = "hd"
    subtitles: bool = True
    # Replace characters that cause trouble in file systems (umlauts, accents, emojis, ":" …).
    normalize_filenames: bool = True
    # Channel logos from the MediathekView project, fetched and cached by the server (logos.py); off by default.
    channel_logos: bool = False
    # Search source: the local film list, or MediathekViewWeb's public API.
    source: Literal["filmlist", "mediathekviewweb"] = "filmlist"
    # Naming of single downloads (subscriptions have their own). season_episode takes "(S02/E03)" from the
    # title, else the number "(39)" as season 1, else falls back to the date: the Plex/Jellyfin layout.
    naming_scheme: Literal["episode_number", "season_episode", "date", "template"] = "season_episode"
    naming_template: str | None = None
    # Up to 0.1.0.dev19 the one series library (MTDL_SHOWS_DIR) took these four; now each library has its own
    # (libraries.py) and they only seed library "Serien" when the old state is adopted.
    library_template: str = DEFAULT_TEMPLATE
    library_normalize: bool = False
    plex_refresh: bool = False
    plex_library_path: str | None = Field(default=None, pattern=r"^/")
    # Plex for the rescan after a download into a library (if the library asks for it). The token is a secret.
    plex_url: str | None = Field(default=None, pattern=r"^https?://[^\s/?#]+(/[^\s?#]*)?$")
    # MCP server at /mcp for assistants (mcp_server.py); off by default, and even then only with an API key.
    mcp_enabled: bool = False
    # The first start's guide (Ersteinrichtung) ran or was skipped. Installations in use before it count as set up.
    setup_done: bool = False
    # Subscriptions leave out audio description, sign language and similar versions (special.py);
    # each subscription may say otherwise.
    exclude_special_versions: bool = True

    @field_validator("schedule_days")
    @classmethod
    def _days_once(cls, days: list[int]) -> list[int]:
        return sorted(set(days))

    @model_validator(mode="after")
    def _template_complete(self) -> "AppSettings":
        if self.naming_scheme == "template":
            if not (self.naming_template or "").strip():
                raise ValueError("naming_template is required for the scheme template")
            if bad := unknown_placeholders(self.naming_template or ""):
                raise ValueError(f"unknown placeholders: {', '.join(bad)}")
        if not self.library_template.strip():
            raise ValueError("library_template must not be empty")
        if bad := unknown_library_placeholders(self.library_template):
            raise ValueError(f"unknown placeholders in library_template: {', '.join(bad)}")
        return self

    @property
    def effective_rate(self) -> int:
        return self.bandwidth_limit if self.bandwidth_enabled else 0


class StateStore:
    def __init__(self, path: Path | str) -> None:
        self.db = Database(path)  # shared by the event loop and the thread pool: serialized
        self.db.migrate(MIGRATIONS)

    def close(self) -> None:
        self.db.close()

    def database_bytes(self) -> int:
        (pages,) = self.db.one("PRAGMA page_count") or (0,)
        (size,) = self.db.one("PRAGMA page_size") or (0,)
        return int(pages) * int(size)

    def _get(self, key: str) -> Any:
        row = self.db.one("SELECT value FROM setting WHERE key = ?", (key,))
        return None if row is None else json.loads(row[0])

    def _set(self, key: str, value: Any) -> None:
        self.db.run(
            "INSERT INTO setting (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )

    def settings(self) -> AppSettings:
        return AppSettings.model_validate(self._get("app") or {})

    def update_settings(self, changes: dict[str, Any]) -> AppSettings:
        """Validate and store a partial update; unknown keys and invalid values raise ``ValidationError``."""
        with self.db.transaction():
            merged = AppSettings.model_validate({**self.settings().model_dump(), **changes})
            self._set("app", merged.model_dump())
        return merged

    def secret(self, name: str) -> str | None:
        """Access tokens (TMDB, Plex): stored apart from the settings, never sent back by the API."""
        value = (self._get("secrets") or {}).get(name)
        return value if isinstance(value, str) and value else None

    def set_secret(self, name: str, value: str | None) -> None:
        with self.db.transaction():
            stored = dict(self._get("secrets") or {})
            if value:
                stored[name] = value
            else:
                stored.pop(name, None)
            self._set("secrets", stored)

    def installation_value(self, key: str, make: Any) -> Any:
        """A value fixed once per installation, e.g. the minute of the hour for film list updates."""
        with self.db.transaction():
            value = self._get(key)
            if value is None:
                value = make() if callable(make) else make
                self._set(key, value)
        return value

    def installation_id(self) -> str:
        return str(self.installation_value("installation_id", lambda: secrets.token_hex(8)))

    def marker(self, name: str) -> bool:
        """A switch the installation remembers across starts, e.g. that a one-time reset ran."""
        return bool(self._get(f"marker.{name}"))

    def set_marker(self, name: str, on: bool) -> None:
        if on:
            self._set(f"marker.{name}", True)
        else:
            self.db.run("DELETE FROM setting WHERE key = ?", (f"marker.{name}",))

    def mark_set_up_if_in_use(self) -> None:
        """Installations with subscriptions, downloads or libraries skip the guide of the first start."""
        if self.settings().setup_done:
            return
        (in_use,) = self.db.one(
            "SELECT EXISTS (SELECT 1 FROM subscription) OR EXISTS (SELECT 1 FROM download)"
            " OR EXISTS (SELECT 1 FROM library)"
        ) or (0,)
        if in_use:
            self.update_settings({"setup_done": True})
