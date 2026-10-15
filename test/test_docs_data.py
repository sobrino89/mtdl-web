"""The website's data from the code (tools/docs_data.py): the committed OpenAPI document and MCP tool list say what
the code says now, so the API reference and the tool page on the website never describe an older version."""

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tools" / "docs_data.py"


def _tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("docs_data", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", ["openapi.json", "mcp-tools.json"])
def test_committed_data_matches_the_code(name: str) -> None:
    tool = _tool()
    committed = (tool.DATA / name).read_text(encoding="utf-8")
    if committed != tool.render(tool.FILES[name]()):
        pytest.fail(f"website/src/data/{name} is stale, write it anew with tools/docs_data.py")


def test_reference_is_complete() -> None:
    """Every operation has a summary and a tag the document describes; the reference groups and names them."""
    document = json.loads((_tool().DATA / "openapi.json").read_text(encoding="utf-8"))
    described = {tag["name"] for tag in document["tags"]}
    operations = [op for item in document["paths"].values() for op in item.values()]
    assert len(operations) > 50
    for op in operations:
        assert op.get("summary") and len(op["tags"]) == 1 and op["tags"][0] in described, op["operationId"]
    assert document["info"]["version"] == "1" and document["servers"][0]["url"] == "http://mtdl-web:8080"
    scheme = document["components"]["securitySchemes"]["api_key"]
    assert (scheme["type"], scheme["in"], scheme["name"]) == ("apiKey", "header", "X-Api-Key")
    open_routes = {path for path, item in document["paths"].items() for op in item.values() if "security" not in op}
    assert open_routes == {
        "/api/health",
        "/api/v1/auth/status",
        "/api/v1/auth/setup",
        "/api/v1/auth/login",
        "/api/v1/auth/logout",
        "/api/v1/auth/password",
        "/api/v1/auth/sessions/end-others",
    }


def test_mcp_tools_split_by_key() -> None:
    data = json.loads((_tool().DATA / "mcp-tools.json").read_text(encoding="utf-8"))
    tools = {tool["name"]: tool["write"] for tool in data["tools"]}
    assert tools["search"] is False and tools["cancel_downloads"] is True
    assert all(tool["description"] for tool in data["tools"])
