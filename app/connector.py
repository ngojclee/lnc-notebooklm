"""Business MCP Hub connector boundary — delegation contract v1.

The Hub calls POST /mcp with:
  Authorization: Bearer <CONNECTOR_BEARER>
  X-MCP-Hub-Client-ID / -Scopes / -Timestamp / -Nonce / -Signature
  (+ optional X-MCP-Hub-App-Id for v4 envelopes)

Signature = lowercase HMAC-SHA256 hex over:
  v3\n<audience>\n<client_id>\n<ts>\n<nonce>\n<sorted_comma_scopes>
  v4 adds <app_id> line after client_id and X-MCP-Hub-App-Id header.

Rules enforced here (connector-contract.md):
  1. all five delegation headers required
  2. timestamp within 90s
  3. nonce not replayed within the window
  4. HMAC verified with DELEGATION_SECRET
  5. delegated scopes ⊆ CONNECTOR_MAX_SCOPES
  6. per-tool scope check on tools/call; unknown tools deny
  7. tools/list filtered to granted scopes (json_response mode)
"""

import hashlib
import hmac
import json
import os
import time
from collections.abc import Awaitable, Callable

def connector_bearer() -> str:
    from .config import env_or_cfg
    return env_or_cfg("CONNECTOR_BEARER", "connector_bearer")


def delegation_secret() -> str:
    from .config import env_or_cfg
    return env_or_cfg("DELEGATION_SECRET", "delegation_secret")


def connector_audience() -> str:
    return os.environ.get("CONNECTOR_AUDIENCE", "lnc-notebooklm")


MAX_CLOCK_SKEW_S = 90
NONCE_TTL_S = 300

_nonces: dict[str, float] = {}

# scope namespace: notebooklm:{read,write,admin}
DEFAULT_TOOL_SCOPES: dict[str, set[str]] = {
    # read
    "notebook_list": {"notebooklm:read"},
    "notebook_get": {"notebooklm:read"},
    "notebook_describe": {"notebooklm:read"},
    "notebook_query": {"notebooklm:read"},
    "notebook_query_start": {"notebooklm:read"},
    "notebook_query_status": {"notebooklm:read"},
    "cross_notebook_query": {"notebooklm:read"},
    "source_list_drive": {"notebooklm:read"},
    "source_describe": {"notebooklm:read"},
    "source_get_content": {"notebooklm:read"},
    "chat_list": {"notebooklm:read"},
    "chat_get": {"notebooklm:read"},
    "chat_export": {"notebooklm:read"},
    "collection_list": {"notebooklm:read"},
    "note": {"notebooklm:read"},
    "alias": {"notebooklm:read"},
    "usage_get": {"notebooklm:read"},
    "research_status": {"notebooklm:read"},
    "studio_status": {"notebooklm:read"},
    "download_artifact": {"notebooklm:read"},
    "download_all_artifacts": {"notebooklm:read"},
    "export_artifact": {"notebooklm:read"},
    "report": {"notebooklm:read"},
    "notebook_share_status": {"notebooklm:read"},
    "profile": {"notebooklm:read"},
    "label": {"notebooklm:read"},
    "tag": {"notebooklm:read"},
    "server_info": {"notebooklm:read"},
    # write
    "notebook_create": {"notebooklm:write"},
    "notebook_rename": {"notebooklm:write"},
    "source_add": {"notebooklm:write"},
    "source_add_chatgpt_file": {"notebooklm:write"},
    "source_rename": {"notebooklm:write"},
    "source_sync_drive": {"notebooklm:write"},
    "chat_configure": {"notebooklm:write"},
    "chat_save_to_note": {"notebooklm:write"},
    "collection_create": {"notebooklm:write"},
    "collection_edit": {"notebooklm:write"},
    "collection_set_emoji": {"notebooklm:write"},
    "research_start": {"notebooklm:write"},
    "research_import": {"notebooklm:write"},
    "studio_create": {"notebooklm:write"},
    "studio_revise": {"notebooklm:write"},
    "notebook_share_public": {"notebooklm:write"},
    "notebook_share_invite": {"notebooklm:write"},
    "notebook_share_batch": {"notebooklm:write"},
    "pipeline": {"notebooklm:write"},
    "batch": {"notebooklm:write"},
    "save_auth_tokens": {"notebooklm:write"},
    "refresh_auth": {"notebooklm:write"},
    # admin / destructive
    "notebook_delete": {"notebooklm:admin"},
    "source_delete": {"notebooklm:admin"},
    "studio_delete": {"notebooklm:admin"},
    "collection_delete": {"notebooklm:admin"},
}


def configured_scopes() -> dict[str, set[str]]:
    """Runtime tool→scope map: DEFAULT_TOOL_SCOPES merged with config.json
    `tool_scopes` overrides — hot-patch the catalog without a redeploy when
    upstream ships new tools."""
    merged = {k: set(v) for k, v in DEFAULT_TOOL_SCOPES.items()}
    try:
        from .config import load_config

        custom = load_config().get("tool_scopes")
        if isinstance(custom, dict):
            for k, v in custom.items():
                merged[k] = {s.strip() for s in str(v).split(",") if s.strip()}
    except Exception:
        pass
    return merged


def max_scopes() -> set[str]:
    raw = os.environ.get("CONNECTOR_MAX_SCOPES", "")
    return {s.strip() for s in raw.split(",") if s.strip()}


def _purge_nonces(now: float) -> None:
    for n, t in list(_nonces.items()):
        if now - t > NONCE_TTL_S:
            _nonces.pop(n, None)


def _signature_ok(headers, secret: str, audience: str) -> bool:
    scopes = sorted({s.strip() for s in headers["x-mcp-hub-client-scopes"].split(",") if s.strip()})
    app_id = headers.get("x-mcp-hub-app-id")
    lines = [
        "v4" if app_id else "v3",
        audience,
        headers["x-mcp-hub-client-id"],
        *([app_id] if app_id else []),
        headers["x-mcp-hub-client-timestamp"],
        headers["x-mcp-hub-client-nonce"],
        ",".join(scopes),
    ]
    expected = hmac.new(secret.encode(), "\n".join(lines).encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, headers["x-mcp-hub-client-signature"].lower())


def verify_delegation(headers, scope_of_tool: Callable[[str], set[str]] | None = None) -> dict:
    """Validate Hub delegation headers → {ok, error, client_id, scopes, app_id}."""
    if not delegation_secret():
        return {"ok": False, "error": "delegation_not_configured"}
    required = [
        "x-mcp-hub-client-id", "x-mcp-hub-client-scopes", "x-mcp-hub-client-timestamp",
        "x-mcp-hub-client-nonce", "x-mcp-hub-client-signature",
    ]
    missing = [h for h in required if not headers.get(h)]
    if missing:
        return {"ok": False, "error": "delegation_headers_missing", "missing": missing}

    try:
        ts = int(headers["x-mcp-hub-client-timestamp"])
    except ValueError:
        return {"ok": False, "error": "delegation_bad_timestamp"}
    now = time.time()
    if abs(now - ts) > MAX_CLOCK_SKEW_S:
        return {"ok": False, "error": "delegation_timestamp_expired"}

    nonce = headers["x-mcp-hub-client-nonce"]
    _purge_nonces(now)
    if nonce in _nonces:
        return {"ok": False, "error": "delegation_nonce_replayed"}

    if not _signature_ok(headers, delegation_secret(), connector_audience()):
        return {"ok": False, "error": "delegation_signature_invalid"}

    scopes = {s.strip() for s in headers["x-mcp-hub-client-scopes"].split(",") if s.strip()}
    ceiling = max_scopes()
    if ceiling and not scopes <= ceiling:
        return {"ok": False, "error": "delegation_scope_exceeded",
                "over": sorted(scopes - ceiling)}

    _nonces[nonce] = now
    return {
        "ok": True,
        "client_id": headers["x-mcp-hub-client-id"],
        "scopes": scopes,
        "app_id": headers.get("x-mcp-hub-app-id"),
    }


def check_tool_scope(tool: str, delegated: set[str]) -> str | None:
    """Return error code or None when the delegated scopes cover the tool."""
    required = configured_scopes().get(tool)
    if required is None:
        return "tool_not_in_catalog"  # unknown/unclassified → deny
    if not delegated & required:
        return "tool_scope_denied"
    return None
