"""Thin wrappers over upstream notebooklm_tools services.

All functions are sync (upstream is sync) — call them via run_in_executor /
fastapi's threadpool. Never let upstream exceptions leak: convert to
typed strings the route layer maps onto error codes.
"""

import json
import re
import time
from http.cookies import SimpleCookie

from . import authuser as _authuser
from . import config

REQUIRED = ["SID", "HSID", "SSID", "APISID", "SAPISID"]
NOTEBOOK_HOSTS = ("notebook.google.com", "notebooklm.google.com")
DEFAULT_HOST = "notebook.google.com"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")


def save_cookie_header(
    cookie_input,
    profile: str | None = None,
    authuser=None,
    base_host: str | None = None,
) -> dict:
    """Parse cookies, scope them to the NotebookLM host, save, validate live.

    ``cookie_input``: raw ``Cookie:`` header (with/without label), a JSON
    export string, or a list of cookie dicts. Domain info (J2Team / Cookie
    Bridge exports) is honoured: Google issues a DIFFERENT ``OSID`` per
    subdomain, and sending mail's OSID to notebook → ``CookieMismatch``.

    ``authuser``: Google multi-signin index (``?authuser=N``). None keeps the
    stored value.

    Returns {ok, saved, validated, detail, base_host, authuser, account} —
    never raises.
    """
    from notebooklm_tools.services.auth import (
        AuthTokens,
        confirm_auth_via_api,
        save_tokens_to_cache,
    )

    items = _parse_cookie_items(cookie_input)
    host = base_host if base_host in NOTEBOOK_HOSTS else _detect_host(items)
    cookies = scope_cookies(items, host)

    missing = [c for c in REQUIRED if c not in cookies]
    if missing:
        return {"ok": False, "error": "missing_cookies", "missing": missing,
                "hint": "export the .google.com cookie set (or a notebook tab), "
                        "not only the notebook.google.com host cookies"}

    cfg = config.load_config()
    if authuser is not None:
        _authuser.set_authuser(authuser)
        cfg["authuser"] = _authuser.get_authuser()
    n = _authuser.get_authuser()

    tokens = AuthTokens(cookies=cookies, base_host=host, extracted_at=time.time())
    save_tokens_to_cache(tokens, profile_name=profile)
    _reset_mcp_client()

    account = account_email(cookies, host, n)
    cfg["account_email"] = account or ""
    cfg["base_host"] = host
    config.save_config(cfg)

    ok, err = confirm_auth_via_api(profile)
    return {
        "ok": ok,
        "saved": True,
        "validated": ok,
        "detail": err,
        "base_host": host,
        "authuser": n,
        "account": account,
        "cookie_count": len(cookies),
    }


def _parse_cookie_items(cookie_input) -> list[dict]:
    """Any accepted input → [{name, value, domain?, hostOnly?}]."""
    arr = cookie_input
    if isinstance(cookie_input, str):
        raw = cookie_input.strip()
        if not raw.startswith("["):
            return [{"name": k, "value": v} for k, v in _parse_cookies(raw).items()]
        try:
            arr = json.loads(raw)
        except Exception:
            return []
    if not isinstance(arr, list):
        return []
    return [
        {**c, "value": str(c["value"])}
        for c in arr
        if isinstance(c, dict) and c.get("name") and c.get("value") is not None
    ]


def _detect_host(items: list[dict]) -> str:
    """Host the account is signed in on — from host cookies if exported."""
    doms = {str(c.get("domain", "")).lstrip(".") for c in items}
    for h in NOTEBOOK_HOSTS:
        if h in doms:
            return h
    return config.load_config().get("base_host") or DEFAULT_HOST


def _applies(c: dict, host: str) -> bool:
    dom = str(c.get("domain") or "").lstrip(".")
    if not dom:
        return True  # raw header — no scope info, keep
    if c.get("hostOnly"):
        return host == dom
    return host == dom or host.endswith("." + dom)


def scope_cookies(items: list[dict], host: str) -> dict:
    """Exactly what a browser sends to https://<host>/ — most specific wins."""
    best: dict[str, tuple[int, str]] = {}
    for c in items:
        if not _applies(c, host):
            continue
        spec = len(str(c.get("domain") or ""))
        if c["name"] not in best or spec > best[c["name"]][0]:
            best[c["name"]] = (spec, c["value"])
    return {k: v for k, (_, v) in best.items()}


def account_email(cookies: dict, host: str, n: int) -> str | None:
    """Email of the account behind ?authuser=n (None if unreadable)."""
    import httpx

    try:
        r = httpx.get(f"https://{host}/?authuser={n}", cookies=cookies,
                      headers={"user-agent": _UA}, follow_redirects=True, timeout=20)
        m = re.search(r'"oPEP7c":"([^"]+)"', r.text)
        return m.group(1) if m else None
    except Exception:
        return None


def _reset_mcp_client() -> None:
    """Drop upstream's cached client so the next MCP call uses new cookies."""
    try:
        from notebooklm_tools.mcp.tools import _utils

        for name in ("reset_client", "_reset_client", "clear_client"):
            fn = getattr(_utils, name, None)
            if callable(fn):
                fn()
                return
        if hasattr(_utils, "_client"):
            _utils._client = None
    except Exception:
        pass


def _parse_cookies(raw_input: str) -> dict:
    """str → {name: value}. Handles raw header, `Cookie:` label, JSON array."""
    raw = (raw_input or "").strip()
    if raw.startswith("["):
        return {c["name"]: c["value"] for c in _parse_cookie_items(raw)}
    if raw.lower().startswith("cookie:"):
        raw = raw.split(":", 1)[1].strip()
    parsed = SimpleCookie()
    try:
        parsed.load(raw)
    except Exception:
        parsed = SimpleCookie()
    cookies = {k: m.value for k, m in parsed.items()}
    if not cookies:
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                cookies[k.strip()] = v.strip()
    return cookies


def auth_status() -> dict:
    """{profiles, authenticated, cookie_age_days} — cheap, local reads."""
    from notebooklm_tools.services.auth import get_active_auth_mtime
    from notebooklm_tools.services.profiles import list_profiles

    profiles = list_profiles() if callable(list_profiles) else []
    mtime = get_active_auth_mtime()
    age_days = round((time.time() - mtime) / 86400, 1) if mtime else None
    cfg = config.load_config()
    return {
        "profiles": profiles,
        "authenticated": bool(mtime),
        "cookie_age_days": age_days,
        "authuser": _authuser.get_authuser(),
        "account": cfg.get("account_email") or None,
        "base_host": cfg.get("base_host") or None,
    }


def set_authuser(n) -> dict:
    """Switch Google account index without re-pushing cookies."""
    _authuser.set_authuser(n)
    cfg = config.load_config()
    cfg["authuser"] = _authuser.get_authuser()
    config.save_config(cfg)
    _reset_mcp_client()
    return {"ok": True, "authuser": cfg["authuser"]}


def validate_live() -> dict:
    """Confirm credentials actually work against the API (network call)."""
    from notebooklm_tools.services.auth import confirm_auth_via_api

    ok, err = confirm_auth_via_api()
    return {"ok": ok, "detail": err}


def refresh() -> dict:
    """HTTP-only cookie rotation (no browser)."""
    from notebooklm_tools.services.auth_http import refresh_auth_http_only
    from notebooklm_tools.utils.config import get_config

    profile = get_config().auth.default_profile
    try:
        res = refresh_auth_http_only(profile)
        return {"ok": bool(getattr(res, "ok", True)), "detail": str(res)}
    except Exception as e:  # upstream surfaces vary; normalize
        return {"ok": False, "error": type(e).__name__, "detail": str(e)[:400]}


def usage() -> dict:
    from notebooklm_tools.services import usage as usage_svc

    fn = next(
        (n for n in dir(usage_svc) if n.startswith("get") or n == "usage"),
        None,
    )
    if fn is None:
        return {"ok": False, "error": "usage_unavailable"}
    try:
        return {"ok": True, "usage": getattr(usage_svc, fn)()}
    except Exception as e:
        return {"ok": False, "error": type(e).__name__, "detail": str(e)[:400]}
