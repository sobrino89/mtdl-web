"""Who may use the UI and the API. MTDL_AUTH picks the mode:

- ``password`` (default): one account. Until a password exists, the UI asks for one together with a one-time setup
  code that only the container log shows: whoever merely reaches the address cannot claim the instance first.
- ``local``: requests from the own network need no password, everything that looks like it comes from elsewhere
  does (``outside``). For experienced users: a reverse proxy that hides the visitor's address and rewrites the
  host makes every visitor look local. Safe without a proxy, or through a VPN (Tailscale addresses count as local).
- ``proxy``: a reverse proxy with its own sign-in (Authelia, Authentik, oauth2-proxy) passes the user in a header,
  which only MTDL_TRUSTED_PROXIES may set.
- ``none``: no sign-in; the UI warns as soon as a request looks like it comes from outside.

API keys (scripts, other services, assistants over MCP) work in every mode, header ``X-Api-Key``. A key either
reads (``read``: GET and the previews) or may change things too (``write``). Stored are a scrypt hash of the
password and SHA-256 hashes of session tokens, API keys and the setup code: nothing that would have to be decrypted.

Reset a forgotten password: ``python -m mtdl_web.auth reset`` in the container (prints a new setup code), or set
MTDL_PASSWORD and restart.
"""

import base64
import collections
import contextlib
import dataclasses
import hashlib
import hmac
import ipaddress
import logging
import os
import re
import secrets
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from mtdl_web.config import Settings
from mtdl_web.state import StateStore

log = logging.getLogger(__name__)

SESSION_DAYS = 30  # sliding: every use within that time extends it
SESSION_COOKIE = "mtdl_session"
API_KEY_HEADER = "x-api-key"
MIN_PASSWORD = 8
MAX_PASSWORD = 256
# scrypt: 16 MiB and about 50 ms per check; the stored hash names its parameters, so they may change later.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
SETUP_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I: read from a log, typed by hand
TOUCH_EVERY = 3600  # seconds between last-seen updates of a session or key (no write per request)

Via = Literal["session", "local", "proxy", "none"]
KeyScope = Literal["read", "write"]  # write includes read


# -- secrets --------------------------------------------------------------------------------------------------------


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    b64 = base64.b64encode
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${b64(salt).decode()}${b64(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, digest = stored.split("$")
        if kind != "scrypt":
            return False
        expected = base64.b64decode(digest)
        given = hashlib.scrypt(
            password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=len(expected)
        )
    except ValueError:
        return False
    return hmac.compare_digest(given, expected)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _setup_key(code: str) -> str:
    return re.sub(r"[\s-]", "", code).upper()


# -- where a request comes from ----------------------------------------------------------------------------------

# Headers that carry the visitor's address, and headers a proxy sets without it.
_ADDRESS_HEADERS = (
    "x-forwarded-for",
    "x-real-ip",
    "cf-connecting-ip",
    "true-client-ip",
    "x-client-ip",
    "x-cluster-client-ip",
    "fastly-client-ip",
)
_PROXY_HEADERS = ("forwarded", "x-forwarded-host", "x-forwarded-proto", "x-forwarded-port", "x-forwarded-server", "via")
# Names only the own network resolves: suffixes the root zone does not delegate, and fritz.box, which routers of that
# brand answer themselves and whose public zone belongs to their maker. Not ".ts.net": Tailscale Funnel publishes
# such names on the internet.
_LOCAL_SUFFIXES = (
    ".local",
    ".lan",
    ".home.arpa",
    ".internal",
    ".localhost",
    ".localdomain",
    ".home",
    ".corp",
    ".intranet",
    ".private",
    ".fritz.box",
)
_FORWARDED_FOR = re.compile(r"for=\s*(\"[^\"]*\"|[^;,\s]+)", re.IGNORECASE)


def _ip(text: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    text = text.strip().strip('"')
    if text.startswith("[") and "]" in text:  # [2001:db8::1]:443
        text = text[1 : text.index("]")]
    elif text.count(":") == 1:  # 192.0.2.1:443
        text = text.split(":", 1)[0]
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return ip.ipv4_mapped
    return ip


def local_address(text: str) -> bool:
    """A private, loopback, link-local or shared (Tailscale, 100.64/10) address; anything else is outside."""
    ip = _ip(text)
    return ip is not None and not ip.is_global


def local_host(host: str) -> bool:
    """A host name (Host header, Origin) of the own network: a local address, localhost, a name without dots or
    with a suffix only local resolvers know. A domain that looks public counts as outside, also when split DNS
    keeps it in the LAN: from the request alone that cannot be told apart."""
    name = host.strip().lower()
    if not name:
        return False
    if name.startswith("[") or _ip(name) is not None:
        return local_address(name)
    if name.count(":") == 1:
        name = name.split(":", 1)[0]
    name = name.rstrip(".")
    return bool(name) and ("." not in name or name.endswith(_LOCAL_SUFFIXES))


def outside(peer: str | None, headers: Mapping[str, str], header_lists: Callable[[str], list[str]]) -> str | None:
    """Why a request looks like it comes from outside the own network, None if nothing does. Every signal counts:
    the direct peer, addresses a proxy added, a proxy without them, and the address the browser used (Host,
    Origin). A proxy that hides the visitor and rewrites the host defeats all of them: then only a password helps."""
    if not peer or not local_address(peer):
        return "peer"
    addresses = [a for name in _ADDRESS_HEADERS for value in header_lists(name) for a in value.split(",") if a.strip()]
    addresses += [m.group(1) for value in header_lists("forwarded") for m in _FORWARDED_FOR.finditer(value)]
    if any(not local_address(a) for a in addresses):
        return "forwarded"
    if not addresses and any(headers.get(name) for name in _PROXY_HEADERS):
        return "proxy"  # a proxy that does not say who asked
    hosts = [h for value in header_lists("x-forwarded-host") for h in value.split(",") if h.strip()]
    if not local_host(headers.get("host", "")) or not all(local_host(h) for h in hosts):
        return "host"
    origin = headers.get("origin", "")
    if origin and origin != "null" and not local_host(urlsplit(origin).netloc):
        return "origin"
    return None


def default_gateways(route: Path = Path("/proc/net/route"), route6: Path = Path("/proc/net/ipv6_route")) -> set[str]:
    """The addresses of the default routes: in a Docker network its bridge. Connections that Docker hands on itself
    (its userland proxy, Docker Desktop, rootless Docker) arrive from there, whoever sent them."""
    found: set[str] = set()
    with contextlib.suppress(OSError, ValueError):
        for line in route.read_text().splitlines()[1:]:
            fields = line.split()  # Iface Destination Gateway Flags …, addresses little-endian hex
            if len(fields) > 3 and fields[1] == "00000000" and int(fields[3], 16) & 0x2:  # RTF_GATEWAY
                found.add(str(ipaddress.IPv4Address(bytes.fromhex(fields[2])[::-1])))
    with contextlib.suppress(OSError, ValueError):
        for line in route6.read_text().splitlines():
            fields = line.split()  # destination, its prefix, source, its prefix, next hop, metric, …, flags, iface
            if len(fields) == 10 and fields[0] == "0" * 32 and fields[1] == "00" and int(fields[8], 16) & 0x2:
                found.add(str(ipaddress.IPv6Address(bytes.fromhex(fields[4]))))
    return found


# -- failed attempts -------------------------------------------------------------------------------------------------


class Throttle:
    """Failed sign-ins: an address waits after LIMIT failures within WINDOW seconds. Beyond GLOBAL failures of all
    addresses together (many guessing at once) every attempt waits a pause after the last failure, one second more
    per failure up to PAUSE: guessing slows to a crawl, but nobody can lock the owner out for long. Kept in memory: a
    restart forgets them, which is fine for minutes-long waits."""

    LIMIT = 5
    GLOBAL = 50
    PAUSE = 30
    WINDOW = 15 * 60

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._by_address: dict[str, collections.deque[float]] = {}
        self._all: collections.deque[float] = collections.deque()

    def _prune(self, times: collections.deque[float], now: float) -> None:
        while times and times[0] <= now - self.WINDOW:
            times.popleft()

    def wait(self, address: str) -> int:
        """Seconds until ``address`` may try again; 0: now."""
        now = self._clock()
        mine = self._by_address.get(address, collections.deque())
        self._prune(mine, now)
        self._prune(self._all, now)
        waits = [0.0]
        if len(mine) >= self.LIMIT:
            waits.append(mine[-self.LIMIT] + self.WINDOW - now)
        if len(self._all) >= self.GLOBAL:
            waits.append(self._all[-1] + min(self.PAUSE, len(self._all) - self.GLOBAL + 1) - now)
        return int(max(waits) + 0.999)

    def failed(self, address: str) -> None:
        now = self._clock()
        self._by_address.setdefault(address, collections.deque()).append(now)
        self._all.append(now)

    def succeeded(self, address: str) -> None:
        self._by_address.pop(address, None)


# -- the store -------------------------------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class ApiKey:
    id: int
    name: str
    prefix: str  # the first characters, to tell keys apart; the key itself is shown once
    created_at: int
    last_used_at: int | None
    scope: KeyScope


@dataclasses.dataclass(frozen=True, slots=True)
class Session:
    created_at: int
    last_seen: int
    user_agent: str
    address: str


class Access:
    def __init__(self, state: StateStore, settings: Settings, clock: Callable[[], float] = time.time) -> None:
        self._state = state
        self._db = state.db
        self._settings = settings
        self._clock = clock
        self.throttle = Throttle()
        # Mode "none": when a request last looked like it came from outside, and why (the UI warns).
        self.outside_seen: tuple[float, str] | None = None
        self._refused_logged: float | None = None
        self.gateways = default_gateways() if settings.auth == "local" else set()
        self._gateway_logged: float | None = None
        # The password from the environment, hashed once (compared like a stored one).
        self._fixed = hash_password(settings.password) if settings.password else None

    @property
    def mode(self) -> str:
        return self._settings.auth

    @property
    def password_from_env(self) -> bool:
        return self._fixed is not None

    def _now(self) -> int:
        return int(self._clock())

    def note_outside(self, reason: str) -> None:
        """Mode none, a request from outside: logged at most hourly, the UI warns for a day."""
        now = self._clock()
        if self.outside_seen is None or self.outside_seen[0] < now - 3600:
            log.warning("MTDL_AUTH=none, but a request came from outside (%s): set MTDL_AUTH=password", reason)
        self.outside_seen = (now, reason)

    def through_gateway(self, peer: str | None) -> bool:
        """Mode local, a request from the container's gateway: Docker's userland proxy (ports published on IPv6,
        requests of the host itself), Docker Desktop and rootless Docker hand connections on from there, maybe from
        the internet. It does not count as local then; logged at most hourly."""
        ip = _ip(peer or "")
        if ip is None or str(ip) not in self.gateways:
            return False
        now = self._clock()
        if self._gateway_logged is None or self._gateway_logged < now - 3600:
            log.warning(
                "MTDL_AUTH=local: a request came through the container's gateway %s, which hides who sent it (Docker's "
                "userland proxy, Docker Desktop, rootless Docker); it needs the password",
                ip,
            )
            self._gateway_logged = now
        return True

    def note_refused(self, name: str) -> None:
        """Mode none, a request under a name that is not local: refused, logged at most hourly."""
        now = self._clock()
        if self._refused_logged is None or self._refused_logged < now - 3600:
            log.warning(
                "MTDL_AUTH=none: refused a request for %r, a name that is not local; without sign-in mtdl-web answers "
                "only under its address or a local name (or set MTDL_AUTH=password)",
                name[:100],
            )
            self._refused_logged = now

    # -- password and setup ------------------------------------------------------------------------------------------

    def has_password(self) -> bool:
        return self._fixed is not None or self._state.secret("password") is not None

    def needs_setup(self) -> bool:
        return self.mode in ("password", "local") and not self.has_password()

    def check_password(self, password: str) -> bool:
        stored = self._fixed or self._state.secret("password")
        return stored is not None and verify_password(password, stored)

    def set_password(self, password: str) -> None:
        self._state.set_secret("password", hash_password(password))
        self._state.set_secret("setup_code", None)

    def new_setup_code(self) -> str:
        """A one-time code for choosing the first password; only its hash is kept, the caller shows the code."""
        raw = "".join(secrets.choice(SETUP_ALPHABET) for _ in range(8))
        self._state.set_secret("setup_code", _sha256(raw))
        return f"{raw[:4]}-{raw[4:]}"

    def check_setup_code(self, code: str) -> bool:
        stored = self._state.secret("setup_code")
        return stored is not None and hmac.compare_digest(_sha256(_setup_key(code)), stored)

    def reset(self) -> str:
        """Forgotten password: remove it and every session, return a new setup code."""
        self._forget_password()
        return self.new_setup_code()

    def _forget_password(self) -> None:
        self._state.set_secret("password", None)
        self._db.run("DELETE FROM session")

    def reset_on_start(self) -> bool:
        """MTDL_PASSWORD_RESET: forget the password at this start, but only once per setting. While the variable
        stays set, later starts keep the new password; a start without it makes the next one count again. The new
        password then needs the setup code from the log, as on the first start: nobody else can claim the instance
        in between. API keys stay."""
        used = self._state.marker("password_reset_used")
        if not self._settings.password_reset:
            if used:
                self._state.set_marker("password_reset_used", False)
            return False
        if used:
            log.warning("MTDL_PASSWORD_RESET is still set: it worked once already; remove it, the password stays")
            return False
        if self._fixed is not None:
            log.warning("MTDL_PASSWORD_RESET has no effect: MTDL_PASSWORD or MTDL_PASSWORD_FILE sets the password")
            return False
        self._state.set_marker("password_reset_used", True)
        self._forget_password()
        log.warning("MTDL_PASSWORD_RESET: password removed, all sessions ended; remove the variable again")
        return True

    # -- sessions ----------------------------------------------------------------------------------------------------

    def create_session(self, user_agent: str, address: str) -> str:
        token = secrets.token_urlsafe(32)
        now = self._now()
        self._db.run(
            "INSERT INTO session (token_hash, created_at, last_seen, user_agent, address) VALUES (?, ?, ?, ?, ?)",
            (_sha256(token), now, now, user_agent[:300], address[:100]),
        )
        return token

    def session(self, token: str) -> tuple[Session, bool] | None:
        """The session of ``token`` and whether it was just extended (then the cookie should be sent again)."""
        if not token:
            return None
        key = _sha256(token)
        row = self._db.one(
            "SELECT created_at, last_seen, user_agent, address FROM session WHERE token_hash = ?", (key,)
        )
        if row is None:
            return None
        now = self._now()
        if row[1] < now - SESSION_DAYS * 86400:
            self._db.run("DELETE FROM session WHERE token_hash = ?", (key,))
            return None
        touched = row[1] < now - TOUCH_EVERY
        if touched:
            self._db.run("UPDATE session SET last_seen = ? WHERE token_hash = ?", (now, key))
        return Session(*row), touched

    def end_session(self, token: str) -> None:
        self._db.run("DELETE FROM session WHERE token_hash = ?", (_sha256(token),))

    def end_other_sessions(self, token: str) -> int:
        changed, _ = self._db.run("DELETE FROM session WHERE token_hash != ?", (_sha256(token),))
        return changed

    # -- API keys ----------------------------------------------------------------------------------------------------

    def create_key(self, name: str, scope: KeyScope) -> tuple[ApiKey, str]:
        """A new key; the plain key exists only in the return value."""
        plain = "mtdl_" + secrets.token_urlsafe(30)
        now = self._now()
        _, key_id = self._db.run(
            "INSERT INTO api_key (name, key_hash, prefix, created_at, scope) VALUES (?, ?, ?, ?, ?)",
            (name.strip(), _sha256(plain), plain[:12], now, scope),
        )
        return ApiKey(int(key_id or 0), name.strip(), plain[:12], now, None, scope), plain

    def api_keys(self) -> list[ApiKey]:
        rows = self._db.all("SELECT id, name, prefix, created_at, last_used_at, scope FROM api_key ORDER BY id")
        return [ApiKey(*r) for r in rows]

    def check_key(self, plain: str) -> ApiKey | None:
        row = self._db.one(
            "SELECT id, name, prefix, created_at, last_used_at, scope FROM api_key WHERE key_hash = ?",
            (_sha256(plain),),
        )
        if row is None:
            return None
        key = ApiKey(*row)
        now = self._now()
        if key.last_used_at is None or key.last_used_at < now - TOUCH_EVERY:
            self._db.run("UPDATE api_key SET last_used_at = ? WHERE id = ?", (now, key.id))
        return key

    def delete_key(self, key_id: int) -> bool:
        changed, _ = self._db.run("DELETE FROM api_key WHERE id = ?", (key_id,))
        return changed > 0

    # -- proxy mode --------------------------------------------------------------------------------------------------

    def _trusted(self, address: str) -> bool:
        ip = _ip(address)
        return ip is not None and any(ip in network for network in self._settings.trusted_proxies)

    def proxy_user(self, peer: str | None, headers: Mapping[str, str]) -> str | None:
        """The user a trusted signing-in proxy names; None from any other address."""
        if not self._trusted(peer or ""):
            return None
        return headers.get(self._settings.auth_header.lower(), "").strip() or None

    def visitor(self, peer: str | None, forwarded_for: list[str]) -> str:
        """Whom failed sign-ins count for: the peer, or behind a proxy of MTDL_TRUSTED_PROXIES the visitor it names
        (the last address in X-Forwarded-For that is not such a proxy itself; what comes before it, anyone could
        have sent). Otherwise every visitor of a reverse proxy would wait for the failures of one."""
        if not self._trusted(peer or ""):
            return peer or ""
        hops = [hop.strip() for value in forwarded_for for hop in value.split(",") if hop.strip()]
        visitor = next((hop for hop in reversed(hops) if not self._trusted(hop)), None)
        if visitor is None:
            return peer or ""
        ip = _ip(visitor)
        return str(ip) if ip is not None else visitor[:100]


# -- command line: forgotten password ----------------------------------------------------------------------------


def _as_owner(path: str) -> None:
    """Run as the owner of the state database: ``docker exec`` starts as root, and files SQLite creates next to the
    database (journal) would then belong to root and lock out the app."""
    if os.geteuid() != 0:
        return
    st = os.stat(path)
    os.setgid(st.st_gid)
    os.setuid(st.st_uid)


def main(argv: list[str]) -> int:
    if argv != ["reset"]:
        print("usage: python -m mtdl_web.auth reset   (forgotten password: new setup code, all sessions end)")
        return 2
    settings = Settings.from_env()
    _as_owner(str(settings.state_db))
    state = StateStore(settings.state_db)
    try:
        code = Access(state, dataclasses.replace(settings, password=None)).reset()
    finally:
        state.close()
    print(f"Password removed, all sessions ended. Open mtdl-web and choose a new one with the setup code {code}")
    if settings.password:
        print("Note: MTDL_PASSWORD or MTDL_PASSWORD_FILE is set and still wins; remove it and restart.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
