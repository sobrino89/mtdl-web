"""Access (auth.py): the four modes through the real app, with requests from the LAN, from a
Tailscale address and from the internet (TestClient sets the peer address), plus setup, throttle, cross-site
protection, API keys and the reset from the command line."""

import contextlib
import dataclasses
import ipaddress
import logging
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_app import settings_for

from mtdl_web import auth
from mtdl_web.app import create_app
from mtdl_web.auth import Access, Throttle, hash_password, local_host, outside, verify_password
from mtdl_web.config import Settings
from mtdl_web.state import StateStore

LAN = ("192.168.0.20", 51000)
TAILSCALE = ("100.101.102.103", 51000)
INTERNET = ("9.9.9.9", 51000)  # a real public address: documentation ranges (203.0.113.0/24) count as local
PROXY = ("10.0.0.2", 51000)
HOST = "192.168.0.10:8080"  # the address the browser uses in the LAN


@contextlib.contextmanager
def app(tmp_path: Path, peer: tuple[str, int] = LAN, **overrides: object) -> Iterator[TestClient]:
    """The app without background work (no film list needed), as seen from ``peer``."""
    settings = dataclasses.replace(settings_for(tmp_path), **overrides)  # type: ignore[arg-type]
    with TestClient(create_app(settings, background=False), client=peer, base_url=f"http://{HOST}") as client:
        yield client


def setup_code(caplog: pytest.LogCaptureFixture) -> str:
    found: list[str] = re.findall(r"setup code ([A-Z2-9]{4}-[A-Z2-9]{4})", caplog.text)
    assert found, caplog.text
    return found[-1]


def code_of(response_json: object) -> object:
    assert isinstance(response_json, dict)
    detail = response_json["detail"]
    return detail["code"] if isinstance(detail, dict) else detail


# -- secrets and signals ----------------------------------------------------------------------------------------------


def test_passwords_are_scrypt_hashes() -> None:
    stored = hash_password("korrekt pferd batterie")
    assert stored.startswith("scrypt$16384$8$1$") and "korrekt" not in stored
    assert verify_password("korrekt pferd batterie", stored)
    assert not verify_password("korrekt pferd batterie!", stored)
    assert not verify_password("x", "bcrypt$whatever") and not verify_password("x", "kaputt")
    assert hash_password("same") != hash_password("same")  # salted


def test_where_a_request_comes_from() -> None:
    def check(peer: str, **headers: str) -> str | None:
        lowered = {k.lower().replace("_", "-"): v for k, v in headers.items()}
        return outside(peer, lowered, lambda name: [lowered[name]] if name in lowered else [])

    assert check("192.168.0.20", host=HOST) is None
    assert check("100.101.102.103", host="nas:8080") is None  # Tailscale, short MagicDNS name
    assert check("fd7a:115c:a1e0::1", host="nas.lan") is None
    assert check("::ffff:192.168.0.20", host="localhost:8080") is None
    assert check("9.9.9.9", host=HOST) == "peer"
    assert check("testclient", host=HOST) == "peer"  # not an address: outside
    assert check("10.0.0.2", host=HOST, x_forwarded_for="8.8.8.8, 10.0.0.1") == "forwarded"
    assert check("10.0.0.2", host=HOST, forwarded='for="[2606:4700:4700::1111]:4711";proto=https') == "forwarded"
    assert check("10.0.0.2", host=HOST, forwarded="for=unknown") == "forwarded"
    assert check("10.0.0.2", host=HOST, x_forwarded_proto="https") == "proxy"  # a proxy that hides the visitor
    assert check("10.0.0.2", host="mtdl.example.org") == "host"
    assert check("10.0.0.2", host="mtdl.tail1234.ts.net") == "host"  # Funnel publishes such names
    assert check("10.0.0.2", host=HOST, x_forwarded_for="192.168.0.30", x_forwarded_host="mtdl.example.org") == "host"
    assert check("10.0.0.2", host=HOST, origin="https://mtdl.example.org") == "origin"
    assert check("10.0.0.2", host=HOST, x_forwarded_for="192.168.0.30") is None  # a proxy in the LAN, for the LAN
    assert [local_host(h) for h in ("[fe80::1]:80", "box", "box.home.arpa", "example.com", "")] == [
        True,
        True,
        True,
        False,
        False,
    ]


def test_the_throttle_counts_per_address_and_overall() -> None:
    now = [1000.0]
    throttle = Throttle(clock=lambda: now[0])
    for _ in range(Throttle.LIMIT):
        assert throttle.wait("a") == 0
        throttle.failed("a")
    assert 0 < throttle.wait("a") <= Throttle.WINDOW and throttle.wait("b") == 0
    now[0] += Throttle.WINDOW
    assert throttle.wait("a") == 0
    for i in range(Throttle.GLOBAL):
        throttle.failed(f"x{i}")
    assert throttle.wait("fresh") > 0  # many addresses guessing together: everyone waits
    throttle.succeeded("a")


def test_sessions_slide_and_end(tmp_path: Path) -> None:
    now = [1_800_000_000.0]
    state = StateStore(tmp_path / "state.sqlite")
    access = Access(state, settings_for(tmp_path, auth="password"), clock=lambda: now[0])
    token = access.create_session("Firefox", "192.168.0.20")
    assert access.session(token) is not None and access.session("wrong") is None
    now[0] += 20 * 86400
    found = access.session(token)
    assert found is not None and found[1]  # used again: extended, the cookie is sent again
    now[0] += 20 * 86400
    assert access.session(token) is not None  # 40 days old, but used 20 days ago
    now[0] += auth.SESSION_DAYS * 86400 + 1
    assert access.session(token) is None
    state.close()


# -- mode password ----------------------------------------------------------------------------------------------------


def test_password_mode_setup_login_and_logout(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="mtdl_web.runtime")
    with app(tmp_path, auth="password") as api:
        status = api.get("/api/v1/auth/status").json()
        assert (status["mode"], status["setup_required"], status["authenticated"]) == ("password", True, False)
        assert code_of(api.get("/api/v1/info").json()) == "setup_required"
        assert api.get("/api/health").status_code in (200, 503) and api.get("/api/v1/openapi.json").is_success
        code = setup_code(caplog)
        assert "setup code" in caplog.text and code not in str(api.get("/api/v1/auth/status").json())

        wrong = api.post("/api/v1/auth/setup", json={"code": "AAAA-AAAA", "password": "lang genug"})
        assert (wrong.status_code, code_of(wrong.json())) == (403, "setup_code_wrong")
        short = api.post("/api/v1/auth/setup", json={"code": code, "password": "kurz"})
        assert code_of(short.json()) == "password_too_short"
        done = api.post("/api/v1/auth/setup", json={"code": code.lower().replace("-", " "), "password": "lang genug"})
        assert done.status_code == 200 and done.json()["authenticated"] is True
        cookie = api.cookies.get(auth.SESSION_COOKIE)
        assert cookie and api.get("/api/v1/info").is_success
        assert code_of(api.post("/api/v1/auth/setup", json={"code": code, "password": "noch einmal"}).json()) == (
            "already_set_up"
        )

        assert api.post("/api/v1/auth/logout").status_code == 204
        api.cookies.clear()
        assert code_of(api.get("/api/v1/info").json()) == "login_required"
        assert code_of(api.post("/api/v1/auth/login", json={"password": "falsch!!"}).json()) == "password_wrong"
        assert api.post("/api/v1/auth/login", json={"password": "lang genug"}).json()["via"] == "session"
        assert api.get("/api/v1/info").is_success


def test_the_password_changes_and_other_sessions_end(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="mtdl_web.runtime")
    with app(tmp_path, auth="password") as api:
        api.post("/api/v1/auth/setup", json={"code": setup_code(caplog), "password": "erstes Passwort"})
        first = api.cookies.get(auth.SESSION_COOKIE)
        api.cookies.clear()
        api.post("/api/v1/auth/login", json={"password": "erstes Passwort"})
        second = api.cookies.get(auth.SESSION_COOKIE)
        assert first and second and first != second
        wrong = api.post("/api/v1/auth/password", json={"current": "falsch", "password": "zweites Passwort"})
        assert code_of(wrong.json()) == "password_wrong"
        changed = api.post("/api/v1/auth/password", json={"current": "erstes Passwort", "password": "zweites Passwort"})
        assert changed.status_code == 200
        assert api.get("/api/v1/info").is_success  # this session stays
        api.cookies.clear()
        old = {"Cookie": f"{auth.SESSION_COOKIE}={first}"}
        assert code_of(api.get("/api/v1/info", headers=old).json()) == "login_required"  # the other one ended
        api.cookies.clear()
        assert code_of(api.post("/api/v1/auth/login", json={"password": "erstes Passwort"}).json()) == "password_wrong"


def test_too_many_wrong_passwords_wait(tmp_path: Path) -> None:
    with app(tmp_path, auth="password", password="das richtige") as api:
        for _ in range(Throttle.LIMIT):
            assert api.post("/api/v1/auth/login", json={"password": "geraten"}).status_code == 403
        blocked = api.post("/api/v1/auth/login", json={"password": "das richtige"})
        assert blocked.status_code == 429 and blocked.json()["detail"]["retry_after"] > 0


def test_a_password_from_the_environment(tmp_path: Path) -> None:
    with app(tmp_path, auth="password", password="aus der Umgebung") as api:
        status = api.get("/api/v1/auth/status").json()
        assert (status["setup_required"], status["password_from_env"]) == (False, True)
        api.post("/api/v1/auth/login", json={"password": "aus der Umgebung"})
        refused = api.post("/api/v1/auth/password", json={"current": "aus der Umgebung", "password": "ein anderes"})
        assert code_of(refused.json()) == "password_from_env"


def test_changes_only_from_the_own_pages(tmp_path: Path) -> None:
    with app(tmp_path, auth="none") as api:
        cross = api.post("/api/v1/subscriptions/check", headers={"Sec-Fetch-Site": "cross-site"})
        assert (cross.status_code, code_of(cross.json())) == (403, "cross_site")
        foreign = api.post("/api/v1/subscriptions/check", headers={"Origin": "http://evil.example"})
        assert code_of(foreign.json()) == "cross_site"
        assert api.post("/api/v1/subscriptions/check", headers={"Sec-Fetch-Site": "same-origin"}).is_success
        assert api.post("/api/v1/subscriptions/check", headers={"Origin": f"http://{HOST}"}).is_success
        assert api.post("/api/v1/subscriptions/check").is_success  # scripts send neither
        assert api.get("/api/v1/info", headers={"Sec-Fetch-Site": "cross-site"}).is_success  # reading is no change


def test_api_keys_work_in_every_mode(tmp_path: Path) -> None:
    with app(tmp_path, auth="password", password="geheim genug") as api:
        api.post("/api/v1/auth/login", json={"password": "geheim genug"})
        created = api.post("/api/v1/api-keys", json={"name": "Home Assistant", "scope": "write"}).json()
        key = created["key"]
        assert key.startswith("mtdl_") and created["prefix"] == key[:12] and created["scope"] == "write"
        assert [k["name"] for k in api.get("/api/v1/api-keys").json()] == ["Home Assistant"]
        assert "key" not in api.get("/api/v1/api-keys").json()[0]
        api.cookies.clear()
        assert api.get("/api/v1/info", headers={"X-Api-Key": key}).is_success
        assert code_of(api.get("/api/v1/info", headers={"X-Api-Key": key + "x"}).json()) == "api_key_invalid"
        listed = api.get("/api/v1/api-keys", headers={"X-Api-Key": key}).json()
        assert listed[0]["last_used_at"] is not None
        assert api.delete(f"/api/v1/api-keys/{created['id']}", headers={"X-Api-Key": key}).status_code == 204
        assert code_of(api.get("/api/v1/info", headers={"X-Api-Key": key}).json()) == "api_key_invalid"


def test_a_read_key_reads_and_previews(tmp_path: Path) -> None:
    with app(tmp_path) as api:
        created = api.post("/api/v1/api-keys", json={"name": "Grafana"}).json()
        assert created["scope"] == "read"  # the default: least rights
        key = {"X-Api-Key": created["key"]}
        assert api.get("/api/v1/subscriptions", headers=key).is_success
        refused = api.post("/api/v1/subscriptions/check", headers=key)
        assert (refused.status_code, code_of(refused.json())) == (403, "api_key_read_only")
        assert code_of(api.delete(f"/api/v1/api-keys/{created['id']}", headers=key).json()) == "api_key_read_only"
        preview = api.post("/api/v1/naming/preview", headers=key, json={"naming": {"scheme": "date"}, "films": []})
        assert preview.json() == {"paths": []}  # a preview changes nothing
        assert api.post("/api/v1/subscriptions/check").is_success  # without a key mode "none" applies


def test_a_forgotten_password_from_the_command_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = settings_for(tmp_path, auth="password")
    settings.config_dir.mkdir(parents=True)
    state = StateStore(settings.state_db)
    access = Access(state, settings)
    access.set_password("vergessen")
    token = access.create_session("Firefox", "192.168.0.20")
    state.close()
    monkeypatch.setenv("MTDL_CONFIG_DIR", str(settings.config_dir))
    assert auth.main(["reset"]) == 0
    code = re.findall(r"setup code ([A-Z2-9]{4}-[A-Z2-9]{4})", capsys.readouterr().out)[0]
    state = StateStore(settings.state_db)
    access = Access(state, settings)
    assert access.needs_setup() and access.session(token) is None and access.check_setup_code(code)
    state.close()
    assert auth.main([]) == 2


def test_a_forgotten_password_through_the_environment_once(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """MTDL_PASSWORD_RESET: one start forgets the password; while the variable stays
    set, further starts keep the new one; a start without it makes the next one count again. The new password needs
    the setup code from the log, as on the first start. API keys stay."""
    caplog.set_level(logging.WARNING)
    with app(tmp_path, auth="password") as api:
        api.post("/api/v1/auth/setup", json={"code": setup_code(caplog), "password": "vergessen bald"})
        key = api.post("/api/v1/api-keys", json={"name": "Skript"}).json()["key"]
        old = api.cookies.get(auth.SESSION_COOKIE)
    caplog.clear()
    with app(tmp_path, auth="password", password_reset=True) as api:
        assert api.get("/api/v1/auth/status").json()["setup_required"] is True
        assert "MTDL_PASSWORD_RESET: password removed" in caplog.text
        assert code_of(api.get("/api/v1/info", headers={"Cookie": f"{auth.SESSION_COOKIE}={old}"}).json()) == (
            "setup_required"  # every session ended
        )
        assert api.get("/api/v1/info", headers={"X-Api-Key": key}).is_success  # keys stay
        refused = api.post("/api/v1/auth/setup", json={"code": "AAAA-AAAA", "password": "von jemand anderem"})
        assert code_of(refused.json()) == "setup_code_wrong"  # reaching the address is not enough
        api.post("/api/v1/auth/setup", json={"code": setup_code(caplog), "password": "das neue Passwort"})
    caplog.clear()
    with app(tmp_path, auth="password", password_reset=True) as api:  # the variable was forgotten: nothing happens
        assert api.get("/api/v1/auth/status").json()["setup_required"] is False
        assert "still set" in caplog.text and "setup code" not in caplog.text
        assert api.post("/api/v1/auth/login", json={"password": "das neue Passwort"}).is_success
    with app(tmp_path, auth="password") as api:  # removed for one start: armed again
        assert api.get("/api/v1/auth/status").json()["setup_required"] is False
    with app(tmp_path, auth="password", password_reset=True) as api:
        assert api.get("/api/v1/auth/status").json()["setup_required"] is True
    with app(tmp_path, auth="password"):
        pass
    caplog.clear()
    with app(tmp_path, auth="password", password="aus der Umgebung", password_reset=True) as api:
        assert "has no effect" in caplog.text  # MTDL_PASSWORD wins, the reset waits
        assert api.post("/api/v1/auth/login", json={"password": "aus der Umgebung"}).is_success


# -- modes local, proxy and none ----------------------------------------------------------------------------------


def test_local_mode_opens_the_lan_and_tailscale_but_nothing_else(tmp_path: Path) -> None:
    with app(tmp_path, LAN, auth="local") as api:
        status = api.get("/api/v1/auth/status").json()
        assert (status["mode"], status["via"], status["authenticated"]) == ("local", "local", True)
        assert api.get("/api/v1/info").is_success
        through_proxy = {"X-Forwarded-For": "8.8.4.4"}
        assert code_of(api.get("/api/v1/info", headers=through_proxy).json()) == "setup_required"
        assert api.get("/api/v1/info", headers={"Host": "mtdl.example.org"}).status_code == 401
        assert api.get("/api/v1/info", headers={"X-Forwarded-Proto": "https"}).status_code == 401
        # From the LAN the first password needs no setup code: whoever is local is in already.
        assert api.post("/api/v1/auth/password", json={"password": "für unterwegs"}).status_code == 200
        api.cookies.clear()
        assert code_of(api.get("/api/v1/info", headers=through_proxy).json()) == "login_required"
    with app(tmp_path, TAILSCALE, auth="local") as api:
        assert api.get("/api/v1/info", headers={"Host": "nas:8080"}).is_success
    with app(tmp_path, INTERNET, auth="local") as api:
        assert code_of(api.get("/api/v1/info").json()) == "login_required"
        assert api.post("/api/v1/auth/login", json={"password": "für unterwegs"}).json()["via"] == "session"
        assert api.get("/api/v1/info").is_success


def test_proxy_mode_trusts_only_its_proxy(tmp_path: Path) -> None:
    trusted = (ipaddress.ip_network("10.0.0.2/32"),)
    with app(tmp_path, PROXY, auth="proxy", trusted_proxies=trusted) as api:
        signed_in = {"Remote-User": "alice"}
        assert api.get("/api/v1/info", headers=signed_in).is_success
        assert api.get("/api/v1/auth/status", headers=signed_in).json()["user"] == "alice"
        assert code_of(api.get("/api/v1/info").json()) == "login_required"
        assert code_of(api.post("/api/v1/auth/login", json={"password": "x"}).json()) == "no_password_mode"
    with app(tmp_path, LAN, auth="proxy", trusted_proxies=trusted) as api:
        assert api.get("/api/v1/info", headers={"Remote-User": "alice"}).status_code == 401  # set by anyone


def test_none_mode_warns_about_requests_from_outside(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with app(tmp_path, LAN, auth="none") as api:
        assert api.get("/api/v1/info").is_success
        assert api.get("/api/v1/auth/status").json()["outside"] is None
    with app(tmp_path, INTERNET, auth="none") as api:
        assert api.get("/api/v1/info").is_success
        assert api.get("/api/v1/auth/status").json()["outside"] == "peer"
        assert "MTDL_AUTH=none, but a request came from outside (peer)" in caplog.text


def test_the_settings_from_the_environment(tmp_path: Path) -> None:
    password_file = tmp_path / "password"
    password_file.write_text("aus der Datei\n")
    settings = Settings.from_env({"MTDL_PASSWORD_FILE": str(password_file), "TZ": "Europe/Berlin"})
    assert (settings.auth, settings.password, settings.password_reset) == ("password", "aus der Datei", False)
    assert Settings.from_env({"MTDL_PASSWORD_RESET": "true"}).password_reset is True
    proxy = Settings.from_env({"MTDL_AUTH": "proxy", "MTDL_TRUSTED_PROXIES": "10.0.0.2, 172.18.0.0/16"})
    assert [str(n) for n in proxy.trusted_proxies] == ["10.0.0.2/32", "172.18.0.0/16"]
    with pytest.raises(ValueError, match="MTDL_TRUSTED_PROXIES"):
        Settings.from_env({"MTDL_AUTH": "proxy"})
    with pytest.raises(ValueError, match="MTDL_AUTH"):
        Settings.from_env({"MTDL_AUTH": "lokal"})
    assert "aus der Datei" not in repr(settings)
