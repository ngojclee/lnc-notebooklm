# lnc-notebooklm

Internal Gemini Notebook (NotebookLM) service for the lengoc.me fleet —
wraps [`notebooklm-mcp-cli`](https://github.com/jacob-bd/notebooklm-mcp-cli)
(pinned, vendored at `F:\_agentsync\repos\notebooklm-mcp-cli`) as a
Portainer-deployed HTTP service per `lnc-internal-app-blueprint` (Shape 0).

## Surfaces

| Path | Auth | Purpose |
|---|---|---|
| `/mcp` | `Authorization: Bearer` | Upstream streamable-http MCP — all 53 tools (notebooks, sources, query, studio, research…) |
| `/admin` | session cookie | Ops UI: status, credential drop, inbox, settings |
| `/login.html` | — | login entry (management key) |
| `/api/files` | Bearer or session | Upload files → container inbox → pass `path` to `source_add(source_type="file")` |
| `/api/admin/credentials` | session | Paste Google `Cookie:` header → save + live-validate |
| `/api/admin/refresh` | session | HTTP-only cookie rotation (no browser in image) |
| `/api/admin/usage` | session | Plan usage windows |
| `/api/admin/rpc-overrides` | session | Hot-patch rotated `NOTEBOOKLM_RPC_OVERRIDES` |
| `/api/status`, `/health`, `/api/auth/status` | varies | health/status |

## Auth model

- `MANAGEMENT_KEY` env → `/api/auth/login` → HMAC session cookie (admin UI).
- `LNC_API_KEYS` env (comma-separated) → Bearer keys for `/mcp` + `/api/files`.
- Google session cookies → pasted once via `/admin` (or `save_auth_tokens`
  MCP tool), stored by upstream under `NOTEBOOKLM_MCP_CLI_PATH` (`/data/nlm`).

## Agent wiring

Remote MCP → per-agent stdio bridge (house `remote-mcp-stdio-proxy.mjs`
pattern, see `lnc-mcp-shared`): point `MCP_HTTP_URL`/endpoint at
`http://<host>:<port>/mcp` with `Authorization: Bearer <key>`.

## Local dev

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
$env:MANAGEMENT_KEY='dev'; $env:LNC_API_KEYS='dev-key'
.venv/Scripts/python -m uvicorn app.main:app --port 8000
```

## Deploy

`release-portainer-app` flow — repo-build mode: Portainer stack **Repository**
→ URL `https://gitea.lengoc.me/apps/lnc-notebooklm`, compose path `stack.yml`,
env `MANAGEMENT_KEY` + `LNC_API_KEYS` (+ optional `DATA_DIR`, `PORT`,
`PUBLIC_BASE_URL`). Then NPM proxy host + internal DNS (`notebooklm.lengoc.me`).

Env vars: see header comments in `stack.yml`.
