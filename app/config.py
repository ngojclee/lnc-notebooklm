"""Environment parsing and runtime config — blueprint 3-tier layering.

Tier 1: env parsing → typed constants at boot (this module top).
Tier 2: runtime tunables → JSON config at DATA_DIR/config.json, editable via /admin.
Tier 3: secrets → env only (MANAGEMENT_KEY, LNC_API_KEYS) — never persisted.
"""

import hashlib
import json
import os
import secrets
import tempfile
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "./data")).resolve()
INBOX_DIR = DATA_DIR / "inbox"
CONFIG_PATH = DATA_DIR / "config.json"
PORT = int(os.environ.get("PORT", "8000"))
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")


def _cfg_value(key: str) -> str:
    """UI-settable value persisted in /data/config.json (credential-bearing volume)."""
    return str(load_config().get(key) or "").strip()


def env_or_cfg(env_name: str, cfg_key: str) -> str:
    """Precedence: env wins (deployment-managed), else UI-persisted config."""
    return os.environ.get(env_name, "").strip() or _cfg_value(cfg_key)


def allowed_hosts() -> list[str]:
    """ALLOWED_HOSTS env — comma-separated, '*' or empty = accept all (internal app).

    Mirrors GA_ALLOWED_HOSTS pattern: list every name/IP clients may use,
    e.g. "notebooklm.lengoc.me,lnc-notebooklm,10.21.4.101".
    """
    raw = os.environ.get("ALLOWED_HOSTS", "").strip()
    if not raw or raw == "*":
        return ["*"]
    return [h.strip() for h in raw.split(",") if h.strip()]


def public_base(request) -> str:
    """Base URL for generated links — PUBLIC_BASE_URL env wins, else
    UI-saved `public_base_url`, else derive from the request Host (works
    for IP *and* domain access; scheme honors X-Forwarded-Proto via
    --proxy-headers)."""
    fixed = PUBLIC_BASE_URL or _cfg_value("public_base_url")
    if fixed:
        return fixed
    return str(request.base_url).rstrip("/")


COOKIE_NAME = "nblm_session"
SESSION_TTL_S = 12 * 3600

# Upstream storage lands inside the data volume.
os.environ.setdefault("NOTEBOOKLM_MCP_CLI_PATH", str(DATA_DIR / "nlm"))


def _bootstrap_mgmt_key() -> str:
    """MANAGEMENT_KEY env wins; else generate once, persist to config, log once."""
    env_key = os.environ.get("MANAGEMENT_KEY", "").strip()
    if env_key:
        return env_key
    cfg = load_config()
    key = cfg.get("_bootstrap_mgmt_key")
    if not key:
        key = secrets.token_urlsafe(24)
        cfg["_bootstrap_mgmt_key"] = key
        save_config(cfg)
        print(
            "[lnc-notebooklm] MANAGEMENT_KEY not set — generated bootstrap key "
            "(persisted in config.json, rotate by setting MANAGEMENT_KEY): "
            + key,
            flush=True,
        )
    return key


def _api_keys() -> set[str]:
    raw = os.environ.get("LNC_API_KEYS", "")
    return {k.strip() for k in raw.split(",") if k.strip()}


MANAGEMENT_KEY = _bootstrap_mgmt_key()
API_KEYS = _api_keys()


# ------------------------------------------------- UI-managed API keys
# Env keys (LNC_API_KEYS) always work. UI keys live in config.json as
# sha256 hashes + prefix only — plaintext is shown once at creation,
# never stored or readable back.

def cfg_key_records() -> list[dict]:
    keys = load_config().get("api_keys")
    return [r for r in keys if isinstance(r, dict)] if isinstance(keys, list) else []


def _write_key_records(keys: list[dict]) -> None:
    cfg = load_config()
    if keys:
        cfg["api_keys"] = keys
    else:
        cfg.pop("api_keys", None)
    save_config(cfg)


def add_api_key(label: str) -> dict:
    key = "nblm_" + secrets.token_urlsafe(24)
    rec = {
        "id": secrets.token_hex(4), "label": label, "prefix": key[:14],
        "sha256": hashlib.sha256(key.encode()).hexdigest(),
        "enabled": True, "created": int(time.time()),
    }
    _write_key_records(cfg_key_records() + [rec])
    return {**rec, "secret": key}


def update_api_key(key_id: str, **fields) -> dict | None:
    keys = cfg_key_records()
    for r in keys:
        if r.get("id") == key_id:
            if "enabled" in fields:
                r["enabled"] = bool(fields["enabled"])
            if "label" in fields:
                r["label"] = str(fields["label"])
            _write_key_records(keys)
            return r
    return None


def delete_api_key(key_id: str) -> bool:
    keys = cfg_key_records()
    keep = [r for r in keys if r.get("id") != key_id]
    if len(keep) == len(keys):
        return False
    _write_key_records(keep)
    return True


@dataclass
class RuntimeConfig:
    rpc_overrides: dict = field(default_factory=dict)
    default_notebook_id: str = ""


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def save_config(cfg: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(DATA_DIR), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            json.dump(cfg, tmp, indent=1, ensure_ascii=False)
        os.replace(tmp_name, CONFIG_PATH)
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass


def get_runtime() -> RuntimeConfig:
    cfg = load_config()
    return RuntimeConfig(
        rpc_overrides=cfg.get("rpc_overrides") or {},
        default_notebook_id=cfg.get("default_notebook_id", ""),
    )


def apply_rpc_overrides(overrides: dict | None) -> None:
    cfg = load_config()
    if overrides:
        cfg["rpc_overrides"] = overrides
        os.environ["NOTEBOOKLM_RPC_OVERRIDES"] = json.dumps(overrides)
    else:
        cfg.pop("rpc_overrides", None)
        os.environ.pop("NOTEBOOKLM_RPC_OVERRIDES", None)
    save_config(cfg)


def apply_stored_overrides() -> None:
    stored = get_runtime().rpc_overrides
    if stored:
        os.environ["NOTEBOOKLM_RPC_OVERRIDES"] = json.dumps(stored)


def _secret_state(env_name: str) -> str:
    if os.environ.get(env_name, "").strip():
        return "set (env)"
    if _cfg_value(env_name.lower()):
        return "set (ui)"
    return "unset"


def masked_config() -> dict:
    """Config for /api/settings — secrets never leave this process."""
    rt = get_runtime()
    return {
        "rpc_overrides": rt.rpc_overrides,
        "default_notebook_id": rt.default_notebook_id,
        "data_dir": str(DATA_DIR),
        "public_base_url": env_or_cfg("PUBLIC_BASE_URL", "public_base_url") or "(auto from request)",
        "allowed_hosts": allowed_hosts(),
        "env": {
            "MANAGEMENT_KEY": "********" if os.environ.get("MANAGEMENT_KEY") else "(generated)",
            "LNC_API_KEYS": f"{len(_api_keys())} env + {len(cfg_key_records())} ui key(s)",
            "CONNECTOR_BEARER": _secret_state("CONNECTOR_BEARER"),
            "DELEGATION_SECRET": _secret_state("DELEGATION_SECRET"),
            "NOTEBOOKLM_MCP_CLI_PATH": os.environ.get("NOTEBOOKLM_MCP_CLI_PATH"),
            "NOTEBOOKLM_DISABLE_HEADLESS_REFRESH": os.environ.get(
                "NOTEBOOKLM_DISABLE_HEADLESS_REFRESH", "0"
            ),
        },
    }
