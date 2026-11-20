import asyncio
import time

import pytest

from freecad_mcp import aec_tools


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setattr(aec_tools, "WORKSPACE", tmp_path.resolve())
    return tmp_path.resolve()


def test_paths_stay_in_workspace(ws):
    assert aec_tools._safe_path("a/b.dxf") == ws / "a" / "b.dxf"
    assert aec_tools._safe_path("/a.dxf") == ws / "a.dxf"
    for bad in ["../x", "a/../../x", "..\\x"]:
        with pytest.raises(ValueError):
            aec_tools._safe_path(bad)


def test_private_urls_refused():
    for url in ["http://127.0.0.1/x", "http://10.0.0.5/x", "http://169.254.169.254/latest", "file:///etc/passwd", "ftp://example.com/x"]:
        with pytest.raises(ValueError):
            aec_tools._check_public_url(url)


def test_child_env_has_no_secrets(monkeypatch):
    monkeypatch.setenv("FREECAD_MCP_LISTEN_TOKEN", "x" * 40)
    monkeypatch.setenv("SOME_API_KEY", "k")
    env = aec_tools._child_env()
    assert "FREECAD_MCP_LISTEN_TOKEN" not in env and "SOME_API_KEY" not in env
    assert env["QT_QPA_PLATFORM"] == "offscreen"


def _call(app, path, method="GET"):
    sent = []

    async def receive():
        return {"type": "http.request"}

    async def send(msg):
        sent.append(msg)

    asyncio.run(app({"type": "http", "path": path, "method": method, "headers": []}, receive, send))
    return sent[0]["status"], b"".join(m.get("body", b"") for m in sent[1:])


def test_signed_downloads(ws):
    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 401, "headers": []})
        await send({"type": "http.response.body", "body": b"inner"})

    app = aec_tools.SignedDownloadMiddleware(inner, "t" * 43)
    (ws / "out").mkdir()
    (ws / "out" / "plan.dxf").write_bytes(b"0\nEOF\n")
    secret = aec_tools._download_secret()
    exp = int(time.time()) + 60
    good = f"/files/{exp}/{aec_tools._sign(secret, 'out/plan.dxf', exp)}/out/plan.dxf"
    assert _call(app, good) == (200, b"0\nEOF\n")
    # tampered path, bad signature, expired link, other routes still need the token
    assert _call(app, good.replace("plan.dxf", "other.dxf"))[0] == 404
    assert _call(app, f"/files/{exp}/{'0' * 40}/out/plan.dxf")[0] == 404
    old = int(time.time()) - 1
    assert _call(app, f"/files/{old}/{aec_tools._sign(secret, 'out/plan.dxf', old)}/out/plan.dxf")[0] == 404
    assert _call(app, "/mcp") == (401, b"inner")
    # signature over a path outside the workspace is still refused
    exp2 = exp + 1
    assert _call(app, f"/files/{exp2}/{aec_tools._sign(secret, '../etc/passwd', exp2)}/../etc/passwd")[0] == 404
