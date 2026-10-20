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

## Google auth gotchas (hard-won, do not re-learn the hard way)

- **One jar, many accounts.** Chrome multi-signin keeps ONE cookie jar for
  all Google accounts in the profile. `SID`/`HSID`/`__Secure-1PSID`… are
  shared; the account is selected **per request** by `?authuser=N` /
  `X-Goog-AuthUser`. There is no per-account cookie to extract — never hunt
  for "the cookies of account 1". We carry `authuser` in app config and
  `app/authuser.py` rewrites upstream httpx requests (upstream hardcodes
  `authuser=0` — without the patch every RPC silently hits account 0).
- **Per-subdomain OSIDs collide.** Google issues a DIFFERENT `OSID` (and
  `__Secure-OSID`, `OTZ`, `COMPASS`…) per host: `notebook.google.com`,
  `mail.google.com`, `myaccount.google.com`…. A `.google.com` export holds
  ~4 copies of the same name. Flattening `name=value` into one dict keeps
  an arbitrary (usually wrong) one → Google answers `CookieMismatch`, and
  upstream surfaces it as **"Authentication expired"** even though the
  session is perfectly alive. Rule: never flatten a multi-domain jar —
  keep `{name,value,domain,hostOnly}` and scope to the target host
  (`nblm.scope_cookies` = what a browser would send).
- **`notebook.google.com` vs `notebooklm.google.com`.** Google rebranded;
  an account may be signed in on either host and `OSID` is host-only.
  `AuthTokens.base_host` + `NOTEBOOKLM_BASE_URL`/`profile.base_host` must
  point at the host the cookies came from, else the saved OSID is useless.
  We auto-detect from exported cookie domains (`_detect_host`).
- **Diagnosing "expired":** `302 → accounts.google.com/ServiceLogin` on a
  no-redirect GET is the OSID *bootstrap* dance, NOT proof of death — test
  with `follow_redirects=True` and look at the final URL
  (`accounts.google.com/CookieMismatch` = bad jar, `?pli=1` = alive).
  The account's email sits in the page HTML as `"oPEP7c":"<email>"` —
  fetch with `?authuser=N` to identify which account a jar serves.
- **Cookie pushes must carry domains.** `/api/credentials` accepts
  `{cookies:[{name,value,domain,hostOnly,…}], authuser}` — extension
  `Cookie Bridge` "Tab" mode (`chrome.cookies.getAll({url})`) is the
  canonical source. A bare `cookie_header` only works because the browser
  already filtered it to one host — rebuilding it extension-side from a
  whole-site export reintroduces the collision.
- UI: every `input` type must be covered by the shared input CSS rule
  (`type=number` was forgotten once → unstyled field); toolbar buttons go
  even-width via `.toolbar-equal`.
