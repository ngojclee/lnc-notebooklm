"""Thin wrappers over upstream notebooklm_tools services.

All functions are sync (upstream is sync) — call them via run_in_executor /
fastapi's threadpool. Never let upstream exceptions leak: convert to
typed strings the route layer maps onto error codes.
"""

import time
from http.cookies import SimpleCookie


def save_cookie_header(cookie_header: str, profile: str | None = None) -> dict:
    """Parse a raw Cookie header, save via upstream auth, validate live.

    Returns {ok, saved, validated, detail, email?} — never raises.
    """
    from notebooklm_tools.services.auth import (
        AuthTokens,
        confirm_auth_via_api,
        save_tokens_to_cache,
        validate_cookies,
    )

    parsed = SimpleCookie()
    try:
        parsed.load(cookie_header or "")
    except Exception:
        parsed = SimpleCookie()
    cookies = {k: m.value for k, m in parsed.items()}
    if not cookies:
        for part in (cookie_header or "").split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                cookies[k.strip()] = v.strip()

    required = ["SID", "HSID", "SSID", "APISID", "SAPISID"]
    missing = [c for c in required if c not in cookies]
    if missing:
        return {"ok": False, "error": "missing_cookies", "missing": missing}

    problems = validate_cookies(cookies) if callable(validate_cookies) else []
    tokens = AuthTokens(cookies=cookies, extracted_at=time.time())
    save_tokens_to_cache(tokens, profile_name=profile)

    ok, err = confirm_auth_via_api(profile)
    return {
        "ok": ok,
        "saved": True,
        "validated": ok,
        "detail": err,
        "warnings": problems,
    }


def auth_status() -> dict:
    """{profiles, authenticated, cookie_age_days} — cheap, local reads."""
    from notebooklm_tools.services.auth import get_active_auth_mtime
    from notebooklm_tools.services.profiles import list_profiles

    profiles = list_profiles() if callable(list_profiles) else []
    mtime = get_active_auth_mtime()
    age_days = round((time.time() - mtime) / 86400, 1) if mtime else None
    return {
        "profiles": profiles,
        "authenticated": bool(mtime),
        "cookie_age_days": age_days,
    }


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
