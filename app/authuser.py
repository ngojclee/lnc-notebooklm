"""Multi-account (``authuser``) support for upstream notebooklm_tools.

Google multi-signin keeps ONE cookie jar for every signed-in account; the
account is chosen per request by ``?authuser=N`` / ``X-Goog-AuthUser: N``.
Upstream always talks to account 0 (it even hardcodes ``authuser=0`` on
uploads), so we rewrite outgoing httpx requests to NotebookLM hosts.
"""

import httpx

NOTEBOOK_HOSTS = {
    "notebooklm.google.com",
    "notebook.google.com",
}

_state = {"n": 0}


def set_authuser(n) -> None:
    try:
        _state["n"] = max(0, int(n or 0))
    except (TypeError, ValueError):
        _state["n"] = 0


def get_authuser() -> int:
    return _state["n"]


def _rewrite(request: httpx.Request) -> None:
    n = _state["n"]
    if not n or request.url.host not in NOTEBOOK_HOSTS:
        return
    request.url = request.url.copy_set_param("authuser", str(n))
    request.headers["x-goog-authuser"] = str(n)


def install() -> None:
    if getattr(httpx.Client.send, "_lnc_authuser", False):
        return
    sync_send, async_send = httpx.Client.send, httpx.AsyncClient.send

    def send(self, request, **kw):
        _rewrite(request)
        return sync_send(self, request, **kw)

    async def asend(self, request, **kw):
        _rewrite(request)
        return await async_send(self, request, **kw)

    send._lnc_authuser = asend._lnc_authuser = True
    httpx.Client.send, httpx.AsyncClient.send = send, asend
