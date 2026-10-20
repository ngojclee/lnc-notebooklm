"""Contract tests — no live Google calls; upstream wrapped functions are mocked."""

import os
import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("MANAGEMENT_KEY", "test-mgmt-key")
os.environ.setdefault("LNC_API_KEYS", "test-api-key")
os.environ.setdefault("DATA_DIR", str(Path(__file__).parent / ".tmp-data"))

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import auth, config, main  # noqa: E402

pytestmark = pytest.mark.anyio


@pytest.fixture
async def client():
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
    assert j["ok"] and j["mcp_endpoint"] == "/mcp"


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
