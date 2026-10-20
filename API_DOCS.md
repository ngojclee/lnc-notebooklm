# API_DOCS — lnc-notebooklm

Base: `http://<host>:<port>` · All responses JSON `{ok:bool,...}` or `{"ok":false,"error":"<code>"}`.

## Public

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/auth/login` | `{key}` | `{ok}` + session cookie (rate-limited 8/5min/IP) |
| GET/POST | `/api/auth/logout` | — | `{ok}` clears cookie |
| GET | `/api/auth/status` | — | `{ok, service, version}` |
| GET | `/health` | — | `{ok}` |
| GET | `/login.html` | — | login page |
| GET | `/` | — | 303 → `/admin` |
| GET | `/admin` | — | 303 → `/login.html` if no session, else ops UI |
| GET | `/static/*` | — | UI assets |

## Bearer api_key or session

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/status` | `{ok, service, version, nblm:{state,cookie_age_days,profiles}, inbox_files, mcp_endpoint}` |
| POST | `/api/files` | multipart `file` → `{ok,name,path,size}` — `path` feeds `source_add` |
| GET | `/api/files` | `{files:[{name,path,size,mtime}]}` |
| DELETE | `/api/files/{name}` | basename only; 404 otherwise |

## Session-only (`/api/admin/*`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/admin/auth` | upstream auth state, cookie age, profiles |
| POST | `/api/admin/validate` | live `confirm_auth_via_api` → `{ok,detail}` |
| POST | `/api/admin/credentials` | `{cookies:"SID=…; …"}` → save + validate → `{ok,saved,validated,detail}` |
| POST | `/api/admin/refresh` | HTTP-only cookie rotation |
| GET | `/api/admin/usage` | plan usage windows (upstream passthrough) |
| GET | `/api/settings` | masked config + env summary |
| POST | `/api/admin/rpc-overrides` | `{overrides:{RPC_*:"id"}|null}` → persist + apply |
| POST | `/api/admin/mint-key` | `{key}` to append to `LNC_API_KEYS` env |

## MCP — `/mcp` (Bearer required)

Upstream `notebooklm-mcp` streamable-http transport. `tools/list` exposes
53 tools: `notebook_*`, `source_add`(file|url|text|drive|youtube),
`notebook_query`, `chat_*`, `studio_*`, `research_*`, `download_artifact`,
`save_auth_tokens`, `refresh_auth`, `usage_get`, `profile`, `alias`, …

## Error codes

`invalid_key` `too_many_attempts` `session_required` `bearer_required`
`auth_required` `empty_file` `file_too_large` `not_found`
`missing_cookies` `cookies_required` `overrides_must_be_object` `internal_error`
