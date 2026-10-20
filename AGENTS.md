# AGENTS.md — lnc-notebooklm

Internal lengoc.me app. Follow `lnc-internal-app-blueprint` (Shape 0) and
`lnc-mcp-shared` for anything not stated here.

## Layout

- `app/` — FastAPI service. `main.py` routes+middleware, `auth.py`
  principals (Bearer api_key / HMAC session), `config.py` env+runtime JSON,
  `nblm.py` upstream `notebooklm_tools` wrappers (sync — threadpool only).
- `app/static/` — ops UI (login.html, index.html, app.js, style.css, favicon.*).
- `stack.yml` — THE Portainer deploy unit (repo-build mode, `build: .`, no `image:`).
- `tests/` — contract tests; NEVER call live Google (mock nblm layer).

## Hard rules

- Upstream vendored at `F:\_agentsync\repos\notebooklm-mcp-cli`,
  pinned `notebooklm-mcp-cli==0.15.6` in requirements. Bump = read upstream
  CHANGELOG + bump pin + run tests.
- `NOTEBOOKLM_MCP_CLI_PATH=/data/nlm` — Google cookies live in the volume.
  Never copy cookies/secrets into the repo, logs, or UNC shares.
- `/mcp` must stay Bearer-gated in middleware — the mounted app has no auth.
- File inbox paths returned by `/api/files` are container paths — that is by
  design (`source_add(file_path=...)` reads server-side).
- Version format `x.y.z.k`; bump VERSION + commit together.
