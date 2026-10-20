"""Environment parsing and runtime config — blueprint 3-tier layering.

Tier 1: env parsing → typed constants at boot (this module top).
Tier 2: runtime tunables → JSON config at DATA_DIR/config.json, editable via /admin.
Tier 3: secrets → env only (MANAGEMENT_KEY, LNC_API_KEYS) — never persisted.
"""

import json
import os
import secrets
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "./data")).resolve()
INBOX_DIR = DATA_DIR / "inbox"
CONFIG_PATH = DATA_DIR / "config.json"
PORT = int(os.environ.get("PORT", "8000"))
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")


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
    """Base URL for generated links — PUBLIC_BASE_URL env wins, else derive
    from the request Host (works for IP *and* domain access; scheme honors
    X-Forwarded-Proto via --proxy-headers)."""
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL
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


def masked_config() -> dict:
    """Config for /api/settings — secrets never leave this process."""
    rt = get_runtime()
    return {
        "rpc_overrides": rt.rpc_overrides,
        "default_notebook_id": rt.default_notebook_id,
        "data_dir": str(DATA_DIR),
        "public_base_url": PUBLIC_BASE_URL,
        "allowed_hosts": allowed_hosts(),
        "env": {
            "MANAGEMENT_KEY": "********" if os.environ.get("MANAGEMENT_KEY") else "(generated)",
            "LNC_API_KEYS": f"{len(API_KEYS)} key(s)",
            "CONNECTOR_BEARER": "set" if os.environ.get("CONNECTOR_BEARER") else "unset",
            "DELEGATION_SECRET": "set" if os.environ.get("DELEGATION_SECRET") else "unset",
            "NOTEBOOKLM_MCP_CLI_PATH": os.environ.get("NOTEBOOKLM_MCP_CLI_PATH"),
            "NOTEBOOKLM_DISABLE_HEADLESS_REFRESH": os.environ.get(
                "NOTEBOOKLM_DISABLE_HEADLESS_REFRESH", "0"
            ),
        },
    }
