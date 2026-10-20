"""Contract tests — no live Google calls; upstream wrapped functions are mocked."""

import os
import sys
from pathlib import Path

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("MANAGEMENT_KEY", "test-mgmt-key")
os.environ.setdefault("LNC_API_KEYS", "test-api-key")
os.environ.setdefault("DATA_DIR", str(Path(__file__).parent / ".tmp-data"))

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import auth, config, main  # noqa: E402

pytestmark = pytest.mark.anyio


@pytest.fixture
async def client():
    async with LifespanManager(main.app):
        transport = ASGITransport(app=main.app)
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            yield c


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True


async def test_auth_status_public(client):
    r = await client.get("/api/auth/status")
    j = r.json()
    assert j["ok"] and j["service"] == "lnc-notebooklm"


async def test_mcp_requires_bearer(client):
    r = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert r.status_code == 401
    assert r.json()["error"] == "bearer_required"


async def test_admin_requires_session(client):
    r = await client.get("/api/admin/auth")
    assert r.status_code == 401


async def test_login_sets_cookie(client):
    r = await client.post("/api/auth/login", json={"key": "test-mgmt-key"})
    assert r.status_code == 200
    assert config.COOKIE_NAME in r.cookies


async def test_login_bad_key_401(client):
    r = await client.post("/api/auth/login", json={"key": "wrong"})
    assert r.status_code == 401


async def test_api_status_with_bearer(client, monkeypatch):
    monkeypatch.setattr(
        main.nblm, "auth_status",
        lambda: {"profiles": ["default"], "state": "ok", "cookie_age_days": 1},
    )
    r = await client.get("/api/status", headers={"authorization": "Bearer test-api-key"})
    j = r.json()
    assert j["ok"] and j["mcp_endpoint"].endswith("/mcp") and j["base_url"].startswith("http")


async def test_upload_and_list_files(client, tmp_path):
    config.INBOX_DIR.mkdir(parents=True, exist_ok=True)
    r = await client.post(
        "/api/files",
        headers={"authorization": "Bearer test-api-key"},
        files={"file": ("hello.txt", b"hi")},
    )
    assert r.status_code == 200
    path = Path(r.json()["path"])
    assert path.parent == config.INBOX_DIR and path.exists()

    r2 = await client.get("/api/files", headers={"authorization": "Bearer test-api-key"})
    assert any(f["name"] == path.name for f in r2.json()["files"])

    r3 = await client.delete(
        f"/api/files/{path.name}", headers={"authorization": "Bearer test-api-key"}
    )
    assert r3.json()["ok"] and not path.exists()


async def test_delete_rejects_traversal(client):
    r = await client.delete(
        "/api/files/..%2F..%2Fapp%2Fmain.py",
        headers={"authorization": "Bearer test-api-key"},
    )
    assert r.status_code in (400, 404)


# ------------------------------------------------------- connector contract

import hashlib
import hmac as _hmac
import time

from app import connector  # noqa: E402


def _sign(client_id, scopes, ts, nonce, secret, app_id=None, audience="lnc-notebooklm"):
    scopes = ",".join(sorted(set(s.strip() for s in scopes.split(",") if s.strip())))
    lines = ["v4" if app_id else "v3", audience, client_id]
    if app_id:
        lines.append(app_id)
    lines += [str(ts), nonce, scopes]
    return _hmac.new(secret.encode(), "\n".join(lines).encode(), hashlib.sha256).hexdigest()


_hub_nonce_i = [0]


def _hub_headers(secret, client_id="hub-client", scopes="notebooklm:read,notebooklm:write", ts=None, nonce=None):
    ts = ts or int(time.time())
    _hub_nonce_i[0] += 1
    nonce = nonce or f"n{_hub_nonce_i[0]}"
    sig = _sign(client_id, scopes, ts, nonce, secret)
    return {
        "authorization": "Bearer connector-tok",
        "x-mcp-hub-client-id": client_id,
        "x-mcp-hub-client-scopes": scopes,
        "x-mcp-hub-client-timestamp": str(ts),
        "x-mcp-hub-client-nonce": nonce,
        "x-mcp-hub-client-signature": sig,
        "content-type": "application/json",
        "accept": "application/json",
    }


async def _mcp_session(client, headers, secret, scopes):
    """initialize → session-id → notifications/initialized (fresh nonce each call)."""
    r = await client.post(
        "/mcp",
        json=_rpc("initialize", {"protocolVersion": "2025-03-26",
                                 "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}),
        headers=headers,
    )
    assert r.status_code == 200, r.text
    sid = r.headers["mcp-session-id"]
    h2 = _hub_headers(secret, scopes=scopes) | {"mcp-session-id": sid}
    r2 = await client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                           headers=h2)
    assert r2.status_code in (200, 202)
    return sid


def _rpc(method, params=None, id=1):
    return {"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}}


async def test_connector_requires_delegation_headers(client, monkeypatch):
    monkeypatch.setenv("CONNECTOR_BEARER", "connector-tok")
    monkeypatch.setenv("DELEGATION_SECRET", "secret")
    r = await client.post(
        "/mcp", json=_rpc("initialize", {"protocolVersion": "2025-03-26",
                                       "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}),
        headers={"authorization": "Bearer connector-tok"},
    )
    assert r.status_code == 401 and r.json()["error"] == "delegation_headers_missing"


async def test_connector_bad_signature_rejected(client, monkeypatch):
    monkeypatch.setenv("CONNECTOR_BEARER", "connector-tok")
    monkeypatch.setenv("DELEGATION_SECRET", "secret")
    h = _hub_headers("wrong-secret")
    r = await client.post("/mcp", json=_rpc("tools/list"), headers=h)
    assert r.status_code == 401 and "signature" in r.json()["error"]


async def test_connector_nonce_replay_rejected(client, monkeypatch):
    monkeypatch.setenv("CONNECTOR_BEARER", "connector-tok")
    monkeypatch.setenv("DELEGATION_SECRET", "secret")
    connector._nonces.clear()
    h = _hub_headers("secret", nonce="fixed-nonce")
    r1 = await client.post("/mcp", json=_rpc("tools/list"), headers=h)
    assert r1.status_code != 401  # nonce accepted (RPC-level result irrelevant)
    r2 = await client.post("/mcp", json=_rpc("tools/list"), headers=h)
    assert r2.status_code == 401 and r2.json()["error"] == "delegation_nonce_replayed"


async def test_connector_old_timestamp_rejected(client, monkeypatch):
    monkeypatch.setenv("CONNECTOR_BEARER", "connector-tok")
    monkeypatch.setenv("DELEGATION_SECRET", "secret")
    h = _hub_headers("secret", ts=int(time.time()) - 200)
    r = await client.post("/mcp", json=_rpc("tools/list"), headers=h)
    assert r.json()["error"] == "delegation_timestamp_expired"


async def test_connector_scope_ceiling(client, monkeypatch):
    monkeypatch.setenv("CONNECTOR_BEARER", "connector-tok")
    monkeypatch.setenv("DELEGATION_SECRET", "secret")
    monkeypatch.setenv("CONNECTOR_MAX_SCOPES", "notebooklm:read")
    connector._nonces.clear()
    h = _hub_headers("secret", scopes="notebooklm:read,notebooklm:admin")
    r = await client.post("/mcp", json=_rpc("tools/list"), headers=h)
    assert r.status_code == 401 and r.json()["error"] == "delegation_scope_exceeded"


async def test_connector_tools_list_filtered(client, monkeypatch):
    monkeypatch.setenv("CONNECTOR_BEARER", "connector-tok")
    monkeypatch.setenv("DELEGATION_SECRET", "secret")
    monkeypatch.setenv("CONNECTOR_MAX_SCOPES", "")
    connector._nonces.clear()
    h = _hub_headers("secret", scopes="notebooklm:read")
    sid = await _mcp_session(client, h, "secret", "notebooklm:read")
    h2 = _hub_headers("secret", scopes="notebooklm:read") | {"mcp-session-id": sid}
    r = await client.post("/mcp", json=_rpc("tools/list"), headers=h2)
    assert r.status_code == 200
    tools = [t["name"] for t in r.json()["result"]["tools"]]
    assert "notebook_list" in tools           # read tool kept
    assert "notebook_delete" not in tools     # admin tool filtered
    assert "source_add" not in tools          # write tool filtered


async def test_connector_tool_call_scope_denied(client, monkeypatch):
    monkeypatch.setenv("CONNECTOR_BEARER", "connector-tok")
    monkeypatch.setenv("DELEGATION_SECRET", "secret")
    connector._nonces.clear()
    h = _hub_headers("secret", scopes="notebooklm:read")
    r = await client.post(
        "/mcp",
        json=_rpc("tools/call", {"name": "notebook_delete", "arguments": {"notebook_id": "x", "confirm": True}}),
        headers=h,
    )
    assert r.status_code == 403 and r.json()["error"] == "tool_scope_denied"


async def test_connector_unknown_tool_denied(client, monkeypatch):
    monkeypatch.setenv("CONNECTOR_BEARER", "connector-tok")
    monkeypatch.setenv("DELEGATION_SECRET", "secret")
    connector._nonces.clear()
    h = _hub_headers("secret", scopes="notebooklm:read,notebooklm:write,notebooklm:admin")
    r = await client.post(
        "/mcp",
        json=_rpc("tools/call", {"name": "not_a_tool", "arguments": {}}),
        headers=h,
    )
    assert r.status_code == 403 and r.json()["error"] == "tool_not_in_catalog"


async def test_plain_api_key_still_works(client):
    """Regular agent Bearer bypasses delegation entirely."""
    r = await client.post(
        "/mcp",
        json=_rpc("initialize", {"protocolVersion": "2025-03-26",
                                 "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}),
        headers={"authorization": "Bearer test-api-key"},
    )
    assert r.status_code == 200


async def test_ui_settings_connector_fallback(client, monkeypatch):
    """Empty env → UI-saved connector creds picked up by delegation verify."""
    monkeypatch.delenv("CONNECTOR_BEARER", raising=False)
    monkeypatch.delenv("DELEGATION_SECRET", raising=False)
    # login session
    r = await client.post("/api/auth/login", json={"key": "test-mgmt-key"})
    assert r.status_code == 200
    r = await client.post("/api/admin/settings", json={
        "connector_bearer": "ui-bearer", "delegation_secret": "ui-secret"})
    assert r.status_code == 200
    assert r.json()["settings"]["env"]["CONNECTOR_BEARER"] == "set (ui)"
    # values never echoed
    assert "ui-secret" not in r.text and "ui-bearer" not in r.text
    # delegation now verifies against UI secret
    h = _hub_headers("ui-secret")
    h["authorization"] = "Bearer ui-bearer"
    connector._nonces.clear()
    r = await client.post("/mcp", json=_rpc("tools/list"), headers=h)
    assert r.status_code != 401
    # clearing restores env-only (unset → delegation off)
    r = await client.post("/api/admin/settings", json={
        "connector_bearer": "", "delegation_secret": ""})
    assert r.json()["settings"]["env"]["CONNECTOR_BEARER"] == "unset"


async def test_ui_settings_public_base_override(client):
    r = await client.post("/api/auth/login", json={"key": "test-mgmt-key"})
    assert r.status_code == 200
    await client.post("/api/admin/settings", json={
        "public_base_url": "https://notebooklm.lengoc.me"})
    r = await client.get("/api/status", headers={"authorization": "Bearer test-api-key"})
    assert r.json()["mcp_endpoint"] == "https://notebooklm.lengoc.me/mcp"
    await client.post("/api/admin/settings", json={"public_base_url": ""})
    r = await client.get("/api/status", headers={"authorization": "Bearer test-api-key"})
    assert r.json()["base_url"] == "http://t"


async def test_ui_key_lifecycle(client):
    """create → works → disable → 401 → re-enable → works → revoke → 401."""
    await client.post("/api/auth/login", json={"key": "test-mgmt-key"})
    r = await client.post("/api/admin/keys", json={"label": "codex"})
    assert r.status_code == 200
    key, kid = r.json()["key"], r.json()["item"]["id"]
    assert key.startswith("nblm_")

    client.cookies.clear()  # session would mask revoked-key checks
    H = {"authorization": f"Bearer {key}"}
    assert (await client.get("/api/status", headers=H)).status_code == 200

    await client.post("/api/auth/login", json={"key": "test-mgmt-key"})
    await client.post(f"/api/admin/keys/{kid}/toggle")
    client.cookies.clear()
    assert (await client.get("/api/status", headers=H)).status_code == 401
    await client.post("/api/auth/login", json={"key": "test-mgmt-key"})
    await client.post(f"/api/admin/keys/{kid}/toggle")
    client.cookies.clear()
    assert (await client.get("/api/status", headers=H)).status_code == 200

    # plaintext never readable back
    await client.post("/api/auth/login", json={"key": "test-mgmt-key"})
    r = await client.get("/api/admin/keys")
    rec = next(k for k in r.json()["keys"] if k["id"] == kid)
    assert key not in r.text and rec["prefix"] == key[:14]

    await client.delete(f"/api/admin/keys/{kid}")
    client.cookies.clear()
    assert (await client.get("/api/status", headers=H)).status_code == 401


async def test_rpc_overrides_roundtrip(client, monkeypatch):
    monkeypatch.setenv("NOTEBOOKLM_RPC_OVERRIDES", "")
    async def login(c):
        return (await c.post("/api/auth/login", json={"key": "test-mgmt-key"})).cookies

    cookies = await login(client)
    r = await client.post(
        "/api/admin/rpc-overrides",
        json={"overrides": {"RPC_LIST_NOTEBOOKS": "abc123"}},
        cookies=cookies,
    )
    assert r.json()["rpc_overrides"] == {"RPC_LIST_NOTEBOOKS": "abc123"}
    assert "abc123" in os.environ["NOTEBOOKLM_RPC_OVERRIDES"]
    await client.post("/api/admin/rpc-overrides", json={"overrides": None}, cookies=cookies)
    assert "NOTEBOOKLM_RPC_OVERRIDES" not in os.environ
