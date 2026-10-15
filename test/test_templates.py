"""The templates people start from (compose.yaml, compose.unraid.yaml, .env.example): every variable the app reads
is in one of them and none that it does not; every value in .env.example is the app's own default, so switching a
line on changes nothing until it is edited; and no line hands its comment over as the value (Docker Compose takes
the comment after an unquoted empty value as the value: "MTDL_PASSWORD=   # …" would set that text as password)."""

import re
from pathlib import Path

import pytest

from mtdl_web.config import Settings

ROOT = Path(__file__).resolve().parents[1]
BASICS = {"PUID", "PGID", "UMASK", "TZ"}  # set in the compose files; everything else lives in .env.example
LINE = re.compile(r'^# (?P<name>[A-Z_]+)=(?P<value>"[^"]*"|\S*)(?P<rest>.*)$')


def read_by_the_app() -> set[str]:
    config = (ROOT / "src" / "mtdl_web" / "config.py").read_text(encoding="utf-8")
    entrypoint = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
    names = set(re.findall(r'"(MTDL_[A-Z_]+|TZ)"', config))
    return names | set(re.findall(r"\$\{(MTDL_[A-Z_]+|PUID|PGID|UMASK):-", entrypoint))


def example_lines() -> list[re.Match[str]]:
    lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    return [m for line in lines if (m := LINE.match(line))]


def example() -> dict[str, str]:
    """.env.example with every line switched on, as Docker Compose reads it."""
    return {m["name"]: m["value"].strip('"') for m in example_lines()}


def compose_environment(name: str) -> dict[str, str]:
    text = (ROOT / name).read_text(encoding="utf-8")
    block = text.split("    environment:\n", 1)[1].split("    env_file:", 1)[0]
    return dict(re.findall(r'^      ([A-Z_]+): "?([^"\s]+)"?', block, re.MULTILINE))


def test_every_variable_is_in_a_template() -> None:
    assert read_by_the_app() >= BASICS | {"MTDL_AUTH", "MTDL_TMDB_TOKEN"}  # the scan itself works
    assert set(example()) | BASICS == read_by_the_app()
    for name in ("compose.yaml", "compose.unraid.yaml"):
        assert set(compose_environment(name)) == BASICS, name


def test_switched_on_the_example_changes_nothing() -> None:
    assert Settings.from_env(example()) == Settings.from_env({})


def test_no_comment_becomes_a_value() -> None:
    for m in example_lines():
        assert m["value"] != "" or "#" not in m["rest"], f'{m["name"]}: an empty value needs "" before a comment'


@pytest.mark.parametrize(
    ("name", "basics"),
    [
        ("compose.yaml", {"PUID": "1000", "PGID": "1000", "UMASK": "022", "TZ": "Europe/Berlin"}),
        ("compose.unraid.yaml", {"PUID": "99", "PGID": "100", "UMASK": "000", "TZ": "Europe/Berlin"}),
    ],
)
def test_compose_templates(name: str, basics: dict[str, str]) -> None:
    text = (ROOT / name).read_text(encoding="utf-8")
    assert compose_environment(name) == basics
    assert "      - path: .env " in text and "        required: false" in text
    assert re.search(r"^    image: ghcr\.io/sobrino89/mtdl-web:latest ", text, re.MULTILINE)
    assert "    container_name: mtdl-web\n" in text  # the docs name the container in their commands
