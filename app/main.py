"""lnc-notebooklm — internal NotebookLM (Gemini Notebook) service.

Shape-0 internal app per lnc-internal-app-blueprint:
  /admin            ops UI (session)
  /login.html       login entry
  /api/auth/*       login/logout/status
  /api/admin/*      credential drop, auth status, refresh, usage, rpc overrides
  /api/files*       file inbox for source_add(file_path=...) — api_key or session
  /mcp              upstream notebooklm-mcp streamable-http — Bearer api_key only
"""

import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool
from starlette.staticfiles import StaticFiles

from . import __version__, auth, config, nblm

STATIC_DIR = Path(__file__).parent / "static"
MAX_UPLOAD_MB = int(__import__("os").environ.get("MAX_UPLOAD_MB", "200"))

from notebooklm_tools.mcp.server import mcp as _mcp  # noqa: E402

# Upstream streamable-http MCP, served at /mcp inside the outer app.
# allowed_hosts/allowed_origins are permissive because our middleware
# enforces Bearer auth before any request reaches the MCP app.
_mcp_app = _mcp.http_app(
    path="/mcp",
    allowed_hosts=["*"],
    allowed_origins=["*"],
)


@asynccontextmanager
async def lifespan(_app):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.INBOX_DIR.mkdir(parents=True, exist_ok=True)
    config.apply_stored_overrides()
    async with _mcp_app.lifespan(_app):
        yield


app = FastAPI(title="lnc-notebooklm", version=__version__, lifespan=lifespan)


# ---------------------------------------------------------------- middleware


@app.middleware("http")
async def gate(request: Request, call_next):
    p = request.url.path
    if p == "/admin" or p.startswith("/admin/"):
        if not auth.session_valid(request):
            return RedirectResponse("/login.html", status_code=303)
    elif p.startswith("/mcp"):
        if not auth.bearer_valid(request):
            return _err(401, "bearer_required")
    elif p.startswith("/api/admin"):
        if not auth.session_valid(request):
            return _err(401, "session_required")
    return await call_next(request)


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
        "mcp_endpoint": "/mcp",
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
    return {"ok": True, "name": dest.name, "path": str(dest), "size": len(data)}


@app.get("/api/files")
async def list_files(request: Request):
    auth.require_api_or_session(request)
    files = [
        {"name": f.name, "path": str(f), "size": f.stat().st_size, "mtime": f.stat().st_mtime}
        for f in sorted(config.INBOX_DIR.glob("*"))
        if f.is_file()
    ]
    return {"ok": True, "files": files}


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


@app.post("/api/admin/mint-key")
async def mint_key():
    return {
        "ok": True,
        "key": auth.mint_api_key(),
        "note": "Append to LNC_API_KEYS env (comma-separated) and redeploy.",
    }


# ------------------------------------------------------------------- mounts

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Upstream MCP — mounted LAST so every route above wins; only /mcp falls through.
app.mount("/", _mcp_app)
