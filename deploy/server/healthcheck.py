"""Container health: the MCP server answers HTTP and FreeCAD's add-on is listening."""
import os
import socket
import sys
import urllib.error
import urllib.request

port = int(os.environ.get("PORT", "8796"))
try:
    urllib.request.urlopen(f"http://127.0.0.1:{port}/mcp", timeout=4)
except urllib.error.HTTPError:
    pass  # 401/405/406 = the server is up
except Exception as exc:  # noqa: BLE001
    print(f"mcp server: {exc}")
    sys.exit(1)

try:
    socket.create_connection(("127.0.0.1", 9875), timeout=3).close()
except OSError as exc:
    print(f"freecad rpc: {exc}")
    sys.exit(1)
