"""lnc-notebooklm — internal NotebookLM (Gemini Notebook) service.

Shape-0 internal app per lnc-internal-app-blueprint:
  /admin            ops UI (session)
  /login.html       login entry
  /api/auth/*       login/logout/status
  /api/admin/*      credential drop, auth status, refresh, usage, rpc overrides
  /api/files*       file inbox for source_add(file_path=...) — api_key or session
  /mcp              upstream notebooklm-mcp streamable-http — Bearer api_key only
"""

import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool
from starlette.staticfiles import StaticFiles

from . import __version__, auth, config, connector, nblm

STATIC_DIR = Path(__file__).parent / "static"
MAX_UPLOAD_MB = int(__import__("os").environ.get("MAX_UPLOAD_MB", "200"))

from starlette.middleware.trustedhost import TrustedHostMiddleware  # noqa: E402

from notebooklm_tools.mcp.server import mcp as _mcp  # noqa: E402

# Upstream streamable-http MCP in JSON-response mode (uniform RPC responses
# we can inspect for tools/list filtering). Reached ONLY via our /mcp proxy.
_mcp_app = _mcp.http_app(
    path="/mcp",
    json_response=True,
    allowed_hosts=["*"],
    allowed_origins=["*"],
)
_mcp_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(_app):
    global _mcp_client
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.INBOX_DIR.mkdir(parents=True, exist_ok=True)
    config.apply_stored_overrides()
    async with _mcp_app.lifespan(_app):
        _mcp_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_mcp_app),
            base_url="http://inner",
            timeout=httpx.Timeout(600.0, connect=10.0),
        )
        yield
        await _mcp_client.aclose()
        _mcp_client = None


app = FastAPI(title="lnc-notebooklm", version=__version__, lifespan=lifespan)

# ALLOWED_HOSTS env — accept by domain AND IP (e.g.
# "notebooklm.lengoc.me,lnc-notebooklm,10.21.4.101"); unset/"*" = any host.
if config.allowed_hosts() != ["*"]:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=config.allowed_hosts())


# ---------------------------------------------------------------- middleware


@app.middleware("http")
async def gate(request: Request, call_next):
    p = request.url.path
    if p == "/admin" or p.startswith("/admin/"):
        if not auth.session_valid(request):
            return RedirectResponse("/login.html", status_code=303)
    elif p.startswith("/mcp"):
        token = _bearer_token(request)
        if connector.connector_bearer() and token == connector.connector_bearer():
            # Hub calls: GET/DELETE streams must carry valid delegation too.
            if request.method in ("GET", "DELETE"):
                d = connector.verify_delegation(request.headers)
                if not d["ok"]:
                    return _err(401, d["error"])
        elif not auth.bearer_valid(request):
            return _err(401, "bearer_required")
    elif p.startswith("/api/admin"):
        if not auth.session_valid(request):
            return _err(401, "session_required")
    return await call_next(request)


def _bearer_token(request: Request) -> str:
    a = request.headers.get("authorization", "")
    return a[7:].strip() if a.lower().startswith("bearer ") else ""


def _err(status: int, code: str, **extra):
    return JSONResponse({"ok": False, "error": code, **extra}, status_code=status)


@app.exception_handler(HTTPException)
async def http_exc(_req: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, str) else "http_error"
    return _err(exc.status_code, detail)


@app.exception_handler(Exception)
async def unhandled(_req: Request, exc: Exception):
    return _err(500, "internal_error")


# ------------------------------------------------------------------- public


@app.post("/api/auth/login")
async def login(request: Request, response: Response):
    body = await request.json()
    auth.login(request, response, str(body.get("key", "")))
    return {"ok": True}


@app.api_route("/api/auth/logout", methods=["GET", "POST"])
async def logout(response: Response):
    auth.logout(response)
    return {"ok": True}


@app.get("/api/auth/status")
async def auth_status():
    return {"ok": True, "service": "lnc-notebooklm", "version": __version__}


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/login.html")
async def login_page():
    return FileResponse(STATIC_DIR / "login.html")


@app.get("/")
async def root():
    return RedirectResponse("/admin", status_code=303)


@app.get("/admin")
async def admin_page():
    return FileResponse(STATIC_DIR / "index.html")


# ---------------------------------------------------- api_key-or-session API


@app.get("/api/status")
async def status(request: Request):
    auth.require_api_or_session(request)
    st = await run_in_threadpool(nblm.auth_status)
    files = list(config.INBOX_DIR.glob("*")) if config.INBOX_DIR.exists() else []
    return {
        "ok": True,
        "service": "lnc-notebooklm",
        "version": __version__,
        "nblm": st,
        "inbox_files": len(files),
        "base_url": config.public_base(request),
        "mcp_endpoint": config.public_base(request) + "/mcp",
    }


@app.post("/api/files")
async def upload(request: Request, file: UploadFile):
    auth.require_api_or_session(request)
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty_file")
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, "file_too_large")
    safe = "".join(c for c in Path(file.filename or "file").name if c.isalnum() or c in "._- ")
    dest = config.INBOX_DIR / f"{uuid.uuid4().hex[:8]}-{safe.strip() or 'file'}"
    dest.write_bytes(data)
    base = config.public_base(request)
    return {
        "ok": True, "name": dest.name, "path": str(dest), "size": len(data),
        "url": f"{base}/api/files/{dest.name}",
    }


@app.get("/api/files")
async def list_files(request: Request):
    auth.require_api_or_session(request)
    base = config.public_base(request)
    files = [
        {
            "name": f.name, "path": str(f), "size": f.stat().st_size,
            "mtime": f.stat().st_mtime, "url": f"{base}/api/files/{f.name}",
        }
        for f in sorted(config.INBOX_DIR.glob("*"))
        if f.is_file()
    ]
    return {"ok": True, "files": files}


@app.get("/api/files/{name}")
async def get_file(request: Request, name: str):
    auth.require_api_or_session(request)
    f = config.INBOX_DIR / Path(name).name
    if not f.exists() or f.parent != config.INBOX_DIR or not f.is_file():
        raise HTTPException(404, "not_found")
    return FileResponse(f)


@app.delete("/api/files/{name}")
async def delete_file(request: Request, name: str):
    auth.require_api_or_session(request)
    f = config.INBOX_DIR / Path(name).name
    if not f.exists() or f.parent != config.INBOX_DIR:
        raise HTTPException(404, "not_found")
    f.unlink()
    return {"ok": True}


# ------------------------------------------------------------- admin (session)


@app.get("/api/admin/auth")
async def admin_auth():
    st = await run_in_threadpool(nblm.auth_status)
    return {"ok": True, "auth": st}


@app.post("/api/admin/validate")
async def admin_validate():
    return await run_in_threadpool(nblm.validate_live)


@app.post("/api/admin/credentials")
async def admin_credentials(request: Request):
    body = await request.json()
    cookies = str(body.get("cookies", "")).strip()
    if not cookies:
        raise HTTPException(400, "cookies_required")
    return await run_in_threadpool(nblm.save_cookie_header, cookies)


@app.post("/api/admin/refresh")
async def admin_refresh():
    return await run_in_threadpool(nblm.refresh)


@app.get("/api/admin/usage")
async def admin_usage():
    return await run_in_threadpool(nblm.usage)


@app.get("/api/settings")
async def settings():
    return {"ok": True, "settings": config.masked_config()}


@app.post("/api/admin/rpc-overrides")
async def set_overrides(request: Request):
    body = await request.json()
    overrides = body.get("overrides")
    if overrides is not None and not isinstance(overrides, dict):
        raise HTTPException(400, "overrides_must_be_object")
    await run_in_threadpool(config.apply_rpc_overrides, overrides)
    return {"ok": True, "rpc_overrides": config.get_runtime().rpc_overrides}


UI_SETTINGS_KEYS = ("public_base_url", "connector_bearer", "delegation_secret")


@app.post("/api/admin/settings")
async def set_settings(request: Request):
    """UI-managed settings → /data/config.json. Env vars take precedence when
    set (Portainer-managed); empty-string clears a UI value back to env-only.
    Secrets are write-only: masked in GET /api/settings, never echoed."""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "body_must_be_object")
    cfg = config.load_config()
    for k in UI_SETTINGS_KEYS:
        if k in body:
            v = str(body[k] or "").strip()
            if v:
                cfg[k] = v
            else:
                cfg.pop(k, None)
    await run_in_threadpool(config.save_config, cfg)
    return {"ok": True, "settings": config.masked_config()}


@app.get("/api/admin/keys")
async def list_keys():
    keys = [
        {k: r[k] for k in ("id", "label", "prefix", "enabled", "created") if k in r}
        for r in config.cfg_key_records()
    ]
    return {"ok": True, "keys": keys, "env_keys": len(config._api_keys())}


@app.post("/api/admin/keys")
async def create_key(request: Request):
    body = await request.json()
    rec = auth.mint_api_key(str(body.get("label", "")).strip())
    secret = rec.pop("secret")
    return {"ok": True, "key": secret, "item": rec,
            "note": "Stored as hash — shown once, works immediately, no redeploy needed."}


@app.post("/api/admin/keys/{key_id}/toggle")
async def toggle_key(key_id: str):
    keys = config.cfg_key_records()
    rec = next((r for r in keys if r.get("id") == key_id), None)
    if rec is None:
        raise HTTPException(404, "not_found")
    out = config.update_api_key(key_id, enabled=not rec.get("enabled", True))
    return {"ok": True, "item": out}


@app.delete("/api/admin/keys/{key_id}")
async def revoke_key(key_id: str):
    if not config.delete_api_key(key_id):
        raise HTTPException(404, "not_found")
    return {"ok": True}


@app.post("/api/admin/mint-key")
async def mint_key():
    rec = auth.mint_api_key()
    return {"ok": True, "key": rec.pop("secret"), "item": rec,
            "note": "Saved to config — works immediately."}


# -------------------------------------------------- /mcp — connector boundary
# Business MCP Hub → Bearer CONNECTOR_BEARER + signed X-MCP-Hub-Client-*
# delegation headers → scope-checked + tools/list filtered proxy.
# Internal agents may still use a plain LNC_API_KEYS Bearer (no delegation).
# NOTE: "host" is deliberately forwarded so upstream sees the real
# domain/IP the client used (self-derived URLs, consistent Host checks).

_HOP_HEADERS = {"content-length", "transfer-encoding", "connection"}


@app.api_route("/mcp", methods=["POST"])
async def mcp_proxy(request: Request):
    token = _bearer_token(request)
    delegation = None
    if connector.connector_bearer() and token == connector.connector_bearer():
        delegation = connector.verify_delegation(request.headers)
        if not delegation["ok"]:
            return _err(401, delegation["error"])
    elif not auth.bearer_valid(request):
        return _err(401, "bearer_required")

    body = await request.body()
    rpc = None
    try:
        rpc = json.loads(body) if body else None
    except json.JSONDecodeError:
        pass

    if delegation and isinstance(rpc, dict):
        if rpc.get("method") == "tools/call":
            name = str(rpc.get("params", {}).get("name", ""))
            err = connector.check_tool_scope(name, delegation["scopes"])
            if err:
                return _err(403, err, tool=name)

    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_HEADERS}
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_mcp_app),
            base_url="http://inner",
            timeout=httpx.Timeout(600.0, connect=10.0),
        )
    upstream = await _mcp_client.request(
        request.method, "/mcp", headers=headers, content=body,
        params=request.query_params,
    )

    content = upstream.content
    if (
        delegation and isinstance(rpc, dict)
        and rpc.get("method") == "tools/list"
        and upstream.headers.get("content-type", "").startswith("application/json")
    ):
        try:
            data = json.loads(content)
            result = data.get("result")
            if isinstance(result, dict) and isinstance(result.get("tools"), list):
                result["tools"] = [
                    t for t in result["tools"]
                    if not connector.check_tool_scope(t.get("name", ""), delegation["scopes"])
                ]
                content = json.dumps(data).encode()
        except (json.JSONDecodeError, AttributeError):
            pass

    resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_HEADERS}
    return Response(content=content, status_code=upstream.status_code, headers=resp_headers)


# ------------------------------------------------------------------- mounts

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Upstream MCP — mounted LAST so every route above wins; only /mcp falls through.
app.mount("/", _mcp_app)
