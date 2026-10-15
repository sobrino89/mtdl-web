"""The data the project website takes from the code: the OpenAPI document (API reference) and the MCP tools as an
assistant sees them. Both files are committed next to the website (website/src/data); test_docs_data fails when
they no longer match the code, then this writes them anew (the source from the working copy, not the image's):

    docker run --rm -v "$PWD:/w" -w /w -e PYTHONPATH=/w/src mtdl-web:test python tools/docs_data.py

``--check`` only compares and exits 1 on a difference.
"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from mtdl_web.app import create_app
from mtdl_web.config import Settings
from mtdl_web.mcp_server import INSTRUCTIONS, Mcp

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "website" / "src" / "data"
# The API's version, as in its path (/api/v1). The app's own version changes with every build and would make the
# committed document stale on each one.
API_VERSION = "1"
# For the examples on the website. Only here: in the app's own document a server would send Swagger UI's requests
# there instead of to the instance it runs on.
SERVER = {"url": "http://mtdl-web:8080", "description": "your instance"}


def openapi() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as folder:
        app = create_app(Settings.from_env({"MTDL_CONFIG_DIR": folder, "MTDL_DOWNLOAD_DIR": folder}), background=False)
        document = app.openapi()
    document["info"]["version"] = API_VERSION
    return {"openapi": document.pop("openapi"), "info": document.pop("info"), "servers": [SERVER], **document}


async def _tools() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as folder:
        app = create_app(Settings.from_env({"MTDL_CONFIG_DIR": folder, "MTDL_DOWNLOAD_DIR": folder}), background=False)
        mcp = Mcp(app)
        reading = {tool.name for tool in await mcp.read.list_tools()}
        tools = [
            {
                "name": tool.name,
                "write": tool.name not in reading,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in await mcp.write.list_tools()
        ]
        await mcp.rest.close()
    return {"instructions": INSTRUCTIONS, "tools": tools}


def mcp_tools() -> dict[str, Any]:
    return asyncio.run(_tools())


def render(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


FILES = {"openapi.json": openapi, "mcp-tools.json": mcp_tools}


def main(argv: list[str]) -> int:
    check = "--check" in argv
    stale = []
    for name, make in FILES.items():
        path = DATA / name
        text = render(make())
        if path.exists() and path.read_text(encoding="utf-8") == text:
            continue
        stale.append(name)
        if not check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    verb = "stale" if check else "written"
    print(f"{verb}: {', '.join(stale)}" if stale else "up to date")
    return 1 if check and stale else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
