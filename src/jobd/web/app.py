"""The dashboard app (M7, PRD P4/P4.1).

Two halves and a hard line between them. `api.py` turns the record into JSON —
every result set one call into `jobd.services.dashboard` or
`jobd.services.timeline` (gate 2), every mutation the same service function the
CLI calls. This module is the server: an origin check, the API mounted under
/api, and the built client served for everything else.

The client is a real app now (frontend/, Vite + React + Tailwind + shadcn), so
gate 1 — "paste URL in fresh session, get identical view" — stopped being free
and became something the router has to hold up. It does: every filter is a
query parameter and the client reads its state from the URL rather than from a
store, so there is still no client-side state a URL could fail to capture. The
catch-all below is what makes a deep link work at all — /company/<uuid> is not
a file on disk, and without it a refresh on any page but the root 404s.

Deferred, on purpose (build guide, M7 "Deferred"): the LLM company summary with
citations, semantic search (pgvector is live in the schema and zero rows carry
an embedding, so there is no query path to expose), and outbound. Outbound in
particular is a subsystem, not a button: drafting, sending and scheduled send
need their own migration, a Gmail adapter, a re-consent to a write scope, and a
recorded-approval trail (I1, SECURITY.md §4). The nav reserves its slot and
nothing here can send anything.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from jobd.web.api import router as api_router

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="jobd dashboard")


@app.middleware("http")
async def _same_origin_writes(request: Request, call_next: Any) -> Any:
    """Reject cross-site writes.

    There is no session and no auth here — `jobd serve` binds to localhost for
    one operator — so there is no credential for a forged request to ride on.
    What remains is a blind cross-origin POST from a page the operator happens
    to have open, and an origin check is the defence that actually addresses
    it. A token would add ceremony without closing anything this does not.

    Still correct against a `fetch` client: a browser sends `Origin` on every
    non-GET, including same-origin ones, so the check did not weaken when the
    forms became fetch calls.
    """
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        # The LinkedIn companion extension is the one legitimate cross-origin
        # writer (chrome-extension:// can never match Host). Its endpoint
        # carries its own bearer token (api.py), which is the stronger check —
        # an origin header is trivially absent from a curl, a token is not.
        if request.url.path == "/api/linkedin/push":
            return await call_next(request)
        origin = request.headers.get("origin") or request.headers.get("referer")
        host = request.headers.get("host", "")
        if not origin or host not in origin:
            # Returned, not raised: an exception from inside a middleware's
            # dispatch propagates past FastAPI's handlers instead of becoming
            # a 403, so raising here would surface as a 500 traceback.
            return PlainTextResponse("Cross-origin write refused.", status_code=403)
    return await call_next(request)


@app.exception_handler(StarletteHTTPException)
def _http_error(request: Request, exc: StarletteHTTPException) -> Any:
    """API errors stay JSON; everything else hands back the client.

    A 404 on a page path is a route the client owns (or a genuinely dead
    link), and the client renders it as the app rather than as a bare status —
    the same reason the server-rendered version had an error template.
    """
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            {"detail": str(exc.detail)}, status_code=exc.status_code
        )
    if exc.status_code == 404:
        return _index()
    return PlainTextResponse(str(exc.detail), status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
def _validation_error(request: Request, exc: RequestValidationError) -> Any:
    """A malformed parameter is a bad link, not a 500.

    `/api/company/not-a-uuid` and `?page=abc` land here; both should read as
    "that address doesn't point at anything" rather than a validation dump.
    """
    del exc
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            {"detail": "That address isn't valid — a parameter is malformed."},
            status_code=404,
        )
    return _index()


app.include_router(api_router)


def _index() -> Any:
    """The built client's entry point.

    Missing until `npm run build` has run in frontend/, which is a normal
    state in a fresh checkout and deserves an instruction rather than a
    traceback.
    """
    index = STATIC / "index.html"
    if not index.is_file():
        return PlainTextResponse(
            "The dashboard client has not been built.\n\n"
            "    cd frontend && npm install && npm run build\n\n"
            "The build writes into src/jobd/web/static/, which this server "
            "mounts.",
            status_code=503,
        )
    # No-store on the HTML only: the hashed asset bundle beneath it is
    # immutable and cached hard by the StaticFiles mount, but a stale index
    # would point at asset names that no longer exist after a rebuild.
    return FileResponse(index, headers={"Cache-Control": "no-store"})


if (STATIC / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")


@app.get("/{path:path}")
def client(path: str) -> Any:
    """Every non-API path is the client's to route.

    /company/<uuid> is not a file, and a hard refresh on it has to work — gate
    1 is about addresses surviving a fresh session, which includes this one.
    """
    candidate = (STATIC / path).resolve()
    # Only ever serves files that are really inside the build directory: the
    # path comes from the URL, so `..` has to be excluded by resolution rather
    # than by pattern-matching it out of the string.
    if path and STATIC.resolve() in candidate.parents and candidate.is_file():
        return FileResponse(candidate)
    return _index()
