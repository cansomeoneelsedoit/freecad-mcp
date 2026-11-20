"""End-to-end check of a deployed FreeCAD MCP server, acting as an MCP client.

    FREECAD_MCP_URL=https://cad.example.com/mcp FREECAD_MCP_TOKEN_FILE=~/token \
        python deploy/server/smoke_test.py

Checks: no token -> 401, wrong token -> 401, real token -> tools listed, FreeCAD
healthy, a box is built, and a non-blank screenshot comes back.
"""
import asyncio
import base64
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from mcp import ClientSession

try:  # mcp 2.x
    import httpx2
    from mcp.client.streamable_http import streamable_http_client

    def connect(url: str, headers: dict):
        return streamable_http_client(url, http_client=httpx2.AsyncClient(headers=headers, timeout=300))
except ImportError:  # mcp 1.x
    from mcp.client.streamable_http import streamablehttp_client

    def connect(url: str, headers: dict):
        return streamablehttp_client(url, headers=headers, timeout=60, sse_read_timeout=300)

URL = os.environ.get("FREECAD_MCP_URL", "http://127.0.0.1:8796/mcp")
TOKEN = Path(os.path.expanduser(os.environ["FREECAD_MCP_TOKEN_FILE"])).read_text().strip()
results: list[tuple[str, bool, str]] = []


def _err(r) -> bool:
    return bool(getattr(r, "is_error", None) or getattr(r, "isError", False))


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def status_with(token: str | None) -> int:
    req = urllib.request.Request(URL, method="POST", data=b"{}", headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        return urllib.request.urlopen(req, timeout=20).status
    except urllib.error.HTTPError as e:
        return e.code


async def main() -> None:
    check("no token is refused", status_with(None) == 401)
    check("wrong token is refused", status_with("x" * 43) == 401)

    headers = {"Authorization": f"Bearer {TOKEN}"}
    async with connect(URL, headers) as streams:
        r, w = streams[0], streams[1]
        async with ClientSession(r, w) as s:
            init = await s.initialize()
            check("initialize", True, str(getattr(init, "server_info", None) or getattr(init, "serverInfo", ""))[:80])
            tools = [t.name for t in (await s.list_tools()).tools]
            check("tools listed", "execute_code" in tools and "get_view" in tools, f"{len(tools)} tools")

            st = await s.call_tool("get_rpc_status", {})
            text = " ".join(getattr(c, "text", "") for c in st.content)
            check("FreeCAD add-on reachable", not _err(st) and "running" in text, text[:160].replace("\n", " "))

            code = (
                "import FreeCAD, Part\n"
                "doc = FreeCAD.newDocument('Smoke')\n"
                "b = doc.addObject('Part::Box', 'Box'); b.Length=40; b.Width=20; b.Height=10\n"
                "doc.recompute()\n"
                "print('VOLUME', round(b.Shape.Volume, 1))\n"
            )
            ex = await s.call_tool("execute_code", {"code": code, "include_screenshot": False})
            text = " ".join(getattr(c, "text", "") for c in ex.content)
            check("box built (40x20x10)", not _err(ex) and "8000.0" in text, text[:160].replace("\n", " "))

            view = await s.call_tool("get_view", {"view_name": "Isometric"})
            imgs = [c for c in view.content if getattr(c, "type", "") == "image"]
            size = len(base64.b64decode(imgs[0].data)) if imgs else 0
            if imgs:
                Path("smoke-view.png").write_bytes(base64.b64decode(imgs[0].data))
            check("screenshot returned", size > 5000, f"{size} bytes")

            await s.call_tool("execute_code", {"code": "import FreeCAD\nFreeCAD.closeDocument('Smoke')", "include_screenshot": False})

    failed = [n for n, ok, _ in results if not ok]
    print("ALL PASS" if not failed else f"FAILED: {failed}")
    sys.exit(1 if failed else 0)


asyncio.run(main())
