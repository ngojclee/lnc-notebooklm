"""Auth model — blueprint contract.

Two principals:
  api_key  — `Authorization: Bearer <key>` for /mcp + /api/files + /api/status
  session  — HMAC cookie from POST /api/auth/login for /admin + /api/admin/*

Login is rate-limited (~8 fails / 5min / IP → 429).
"""

import hashlib
import hmac
import secrets
import time
from collections import defaultdict

from fastapi import HTTPException, Request, Response

from . import config
from .config import COOKIE_NAME, MANAGEMENT_KEY, SESSION_TTL_S

_fail_count: dict[str, list[float]] = defaultdict(list)
_MAX_FAILS = 8
_WINDOW_S = 300


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_check(ip: str) -> None:
    now = time.time()
    _fail_count[ip] = [t for t in _fail_count[ip] if now - t < _WINDOW_S]
    if len(_fail_count[ip]) >= _MAX_FAILS:
        raise HTTPException(429, "too_many_attempts")


def _record_fail(ip: str) -> None:
    _fail_count[ip].append(time.time())


def login(request: Request, response: Response, key: str) -> None:
    ip = _client_ip(request)
    _rate_check(ip)
    if not hmac.compare_digest(_sha(key), _sha(MANAGEMENT_KEY)):
        _record_fail(ip)
        raise HTTPException(401, "invalid_key")
    _fail_count.pop(ip, None)
    expiry = int(time.time()) + SESSION_TTL_S
    sig = hmac.new(_sha(MANAGEMENT_KEY).encode(), str(expiry).encode(), hashlib.sha256).hexdigest()
    secure = request.url.scheme == "https"
    response.set_cookie(
        COOKIE_NAME,
        f"{expiry}.{sig}",
        max_age=SESSION_TTL_S,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def logout(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def session_valid(request: Request) -> bool:
    raw = request.cookies.get(COOKIE_NAME, "")
    if "." not in raw:
        return False
    exp_s, sig = raw.rsplit(".", 1)
    try:
        exp = int(exp_s)
    except ValueError:
        return False
    if exp < time.time():
        return False
    want = hmac.new(_sha(MANAGEMENT_KEY).encode(), exp_s.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, want)


def bearer_valid(request: Request) -> bool:
    """UI key records win on a hash match — an adopted env key is governed by
    its record (disable/revoke kills it even while still in env). Otherwise
    fall back to plain env LNC_API_KEYS."""
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return False
    token = auth[7:].strip()
    if not token:
        return False
    token_hash = _sha(token)
    if token_hash in config.revoked_env_hashes():
        return False
    for r in config.cfg_key_records():
        if hmac.compare_digest(token_hash, str(r.get("sha256", ""))):
            return bool(r.get("enabled", True))
    return any(hmac.compare_digest(token_hash, _sha(k)) for k in config._api_keys())


def require_session(request: Request) -> None:
    if not session_valid(request):
        raise HTTPException(401, "session_required")


def require_api_or_session(request: Request) -> str:
    """Files/status endpoints accept either principal."""
    if bearer_valid(request):
        return "api_key"
    if session_valid(request):
        return "session"
    raise HTTPException(401, "auth_required")


def mint_api_key(label: str = "") -> dict:
    """Admin UI: create + persist a UI-managed key (hash stored, secret shown once)."""
    return config.add_api_key(label or "minted")
