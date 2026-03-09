"""PLM Web UI — FastAPI application.

Sub-chunk 8a: Auth + base layout + nav + project list (create / edit / archive).
Sub-chunk 8b: Kanban board — columns + cards with drag-and-drop.
Sub-chunk 8c: Planning page — calendar grid + bar chart.
Sub-chunk 8d: Inbox + Profile + SSE live refresh.

Environment variables (resolved at startup):
  PLM_PASSWORD        — plaintext login password (required; app refuses to start if unset)
  PLM_SESSION_SECRET  — cookie signing key      (required; app refuses to start if unset)
  PLM_DATA_DIR        — override data directory (optional)
  PLM_ROOT_PATH       — e.g. "/plm" when Caddy reverse-proxies at a subpath (optional)
  PLM_PORT            — listening port (optional, default 8000)
"""

# ── 1. Imports ──────────────────────────────────────────────────────────────
import asyncio
import hmac
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import Response

from plm.models.inbox import InboxNote
from plm.models.planning import TimeBlock, WeeklyPlan
from plm.models.profile import BehavioralProfile, ProfileUpdate
from plm.models.project import Project
from plm.storage.store import JsonStore

# ── 2. Env-var capture (validation happens in main()) ───────────────────────
# Captured at module level so routes can reference them without re-reading os.environ.
_PLM_PASSWORD = os.environ.get("PLM_PASSWORD", "")
_PLM_SESSION_SECRET = os.environ.get("PLM_SESSION_SECRET", "")

# ── 3. Module-level singletons ──────────────────────────────────────────────
# store: shared with the MCP server via the same JSON files on disk.
# Swapped by tests via `app_module.store = JsonStore(tmp_dir)`.
store = JsonStore()

_templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))

# _waiting / _loop: used by the SSE endpoint (sub-chunk 8d).
# Each connected browser tab registers an asyncio.Event here; the watchdog
# handler calls _notify_all() from the background thread via call_soon_threadsafe.
_waiting: list[asyncio.Event] = []
_loop: asyncio.AbstractEventLoop | None = None


# ── 4. Watchdog + lifespan (file-watching activated in sub-chunk 8d) ────────
@asynccontextmanager
async def _lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager.

    Sub-chunk 8d will start the watchdog Observer here and stop it on shutdown.
    For 8a–8c this is a no-op placeholder that only captures the event loop so
    the SSE notification path has a reference to it when 8d lands.
    """
    global _loop
    _loop = asyncio.get_event_loop()
    yield
    # 8d: observer.stop() / observer.join() goes here


# ── 5. App + middleware ──────────────────────────────────────────────────────
app = FastAPI(title="Personal Life Manager", lifespan=_lifespan)

# SessionMiddleware signs cookies with itsdangerous — tamper-proof without any
# server-side session storage.  30-day max_age is comfortable for a personal
# tool used daily.  https_only=False keeps local dev and plain-HTTP Pi setups
# working; Caddy adds TLS at the edge.
app.add_middleware(
    SessionMiddleware,
    # If PLM_SESSION_SECRET is empty the app won't start (main() guards it), but
    # we still need a non-empty string here so the middleware constructor doesn't
    # raise at import time during tests that only import the module.
    secret_key=_PLM_SESSION_SECRET or "placeholder-replaced-at-startup",
    max_age=30 * 24 * 3600,
    https_only=False,
)


# ── 6. Auth helpers ──────────────────────────────────────────────────────────
class _NotAuthenticated(Exception):
    """Raised by require_auth; caught by the exception handler below."""


@app.exception_handler(_NotAuthenticated)
async def _redirect_to_login(request: Request, exc: _NotAuthenticated) -> RedirectResponse:
    return RedirectResponse(url=str(request.url_for("login_page")), status_code=303)


async def require_auth(request: Request) -> None:
    """FastAPI dependency: redirects to /login if the session has no auth token."""
    if not request.session.get("authenticated"):
        raise _NotAuthenticated()


def _flash(request: Request, message: str, category: str = "info") -> None:
    """Store a one-shot message in the session for display on the next render."""
    request.session["flash"] = {"message": message, "category": category}


def _render(request: Request, template_name: str, ctx: dict) -> HTMLResponse:
    """
    Render *template_name* with *ctx*.

    Always injects:
      - 'request' so Jinja2's request.url_for() works
      - 'flash'   so base.html can show one-shot messages without boilerplate

    The flash entry is popped from the session so it only appears once.
    """
    ctx.setdefault("request", request)
    ctx["flash"] = request.session.pop("flash", None)
    return templates.TemplateResponse(template_name, ctx)


# ── 7a. Auth routes ──────────────────────────────────────────────────────────

@app.get("/login", name="login_page")
async def login_page(request: Request) -> Response:
    # Already authenticated → skip the login page
    if request.session.get("authenticated"):
        return RedirectResponse(url=str(request.url_for("project_list")), status_code=303)
    return _render(request, "login.html", {})


@app.post("/login")
async def login_submit(
    request: Request,
    password: str = Form(...),
) -> Response:
    # hmac.compare_digest prevents timing attacks (constant-time comparison)
    if _PLM_PASSWORD and hmac.compare_digest(password, _PLM_PASSWORD):
        request.session["authenticated"] = True
        return RedirectResponse(url=str(request.url_for("project_list")), status_code=303)
    _flash(request, "Incorrect password.", "error")
    return _render(request, "login.html", {})


@app.post("/logout", name="logout")
async def logout(request: Request) -> Response:
    request.session.clear()
    return RedirectResponse(url=str(request.url_for("login_page")), status_code=303)


# ── 7b. Project routes (8a) ──────────────────────────────────────────────────

@app.get("/", name="project_list")
async def project_list(
    request: Request,
    _: None = Depends(require_auth),
) -> Response:
    projects = store.list_projects()
    # Pre-compute WIP card counts so the template stays free of model logic
    wip_counts = {p.id: len(p.board.get_wip_cards()) for p in projects}
    active = [p for p in projects if not p.archived]
    archived = [p for p in projects if p.archived]
    return _render(request, "projects.html", {
        "active": active,
        "archived": archived,
        "wip_counts": wip_counts,
    })


@app.post("/projects", name="create_project")
async def create_project(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    target_weekly_hours: str = Form(""),
    _: None = Depends(require_auth),
) -> Response:
    # Empty string → no target (None); a non-numeric value is a user error
    try:
        hours: float | None = float(target_weekly_hours) if target_weekly_hours.strip() else None
    except ValueError:
        _flash(request, "Target hours must be a number (e.g. 10 or 7.5).", "error")
        return RedirectResponse(url=str(request.url_for("project_list")), status_code=303)

    project = Project(
        name=name.strip(),
        description=description.strip(),
        target_weekly_hours=hours,
    )
    store.save_project(project)
    _flash(request, f"Project \"{project.name}\" created.", "success")
    return RedirectResponse(url=str(request.url_for("project_list")), status_code=303)


@app.post("/projects/{project_id}/edit", name="edit_project")
async def edit_project(
    project_id: str,
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    target_weekly_hours: str = Form(""),
    _: None = Depends(require_auth),
) -> Response:
    project = store.get_project(project_id)
    if project is None:
        _flash(request, "Project not found.", "error")
        return RedirectResponse(url=str(request.url_for("project_list")), status_code=303)

    try:
        hours: float | None = float(target_weekly_hours) if target_weekly_hours.strip() else None
    except ValueError:
        _flash(request, "Target hours must be a number (e.g. 10 or 7.5).", "error")
        return RedirectResponse(url=str(request.url_for("project_list")), status_code=303)

    project.name = name.strip()
    project.description = description.strip()
    project.target_weekly_hours = hours
    store.save_project(project)
    _flash(request, f"Project \"{project.name}\" updated.", "success")
    return RedirectResponse(url=str(request.url_for("project_list")), status_code=303)


@app.post("/projects/{project_id}/archive", name="toggle_archive")
async def toggle_archive(
    project_id: str,
    request: Request,
    _: None = Depends(require_auth),
) -> Response:
    project = store.get_project(project_id)
    if project is None:
        _flash(request, "Project not found.", "error")
        return RedirectResponse(url=str(request.url_for("project_list")), status_code=303)

    project.archived = not project.archived
    store.save_project(project)
    action = "archived" if project.archived else "unarchived"
    _flash(request, f"Project \"{project.name}\" {action}.", "success")
    return RedirectResponse(url=str(request.url_for("project_list")), status_code=303)


# ── 7c. Kanban board stub (8b will replace this) ────────────────────────────

@app.get("/projects/{project_id}", name="project_detail")
async def project_detail(
    project_id: str,
    request: Request,
    _: None = Depends(require_auth),
) -> Response:
    # Placeholder — sub-chunk 8b will implement the full Kanban board.
    _flash(request, "Kanban board coming in sub-chunk 8b.", "info")
    return RedirectResponse(url=str(request.url_for("project_list")), status_code=303)


# ── 7d. Planning stub (8c will replace this) ────────────────────────────────

@app.get("/planning", name="planning_page")
async def planning_page(
    request: Request,
    _: None = Depends(require_auth),
) -> Response:
    # Placeholder — sub-chunk 8c will implement the calendar grid.
    _flash(request, "Planning page coming in sub-chunk 8c.", "info")
    return RedirectResponse(url=str(request.url_for("project_list")), status_code=303)


# ── 7e. Inbox + Profile + SSE stubs (8d will replace these) ─────────────────

@app.get("/inbox", name="inbox_page")
async def inbox_page(
    request: Request,
    _: None = Depends(require_auth),
) -> Response:
    _flash(request, "Inbox page coming in sub-chunk 8d.", "info")
    return RedirectResponse(url=str(request.url_for("project_list")), status_code=303)


@app.get("/profile", name="profile_page")
async def profile_page(
    request: Request,
    _: None = Depends(require_auth),
) -> Response:
    _flash(request, "Profile page coming in sub-chunk 8d.", "info")
    return RedirectResponse(url=str(request.url_for("project_list")), status_code=303)


# ── 8. Entry point ───────────────────────────────────────────────────────────

def main() -> None:
    """Entry point for the `plm-web` command."""
    # Fail fast with a clear message rather than a confusing 500 on first request
    if not _PLM_PASSWORD:
        raise SystemExit(
            "PLM_PASSWORD environment variable is required but not set.\n"
            "Example: PLM_PASSWORD=mysecret plm-web"
        )
    if not _PLM_SESSION_SECRET:
        raise SystemExit(
            "PLM_SESSION_SECRET environment variable is required but not set.\n"
            "Example: PLM_SESSION_SECRET=$(python3 -c 'import secrets; print(secrets.token_hex(32))') plm-web"
        )

    port = int(os.environ.get("PLM_PORT", "8000"))

    # root_path tells uvicorn (and FastAPI) the URL prefix under which the app
    # is mounted when behind a reverse proxy like Caddy at /plm/.
    # FastAPI propagates it into request.url_for() automatically, so all
    # template hrefs and redirects work correctly at / or /plm/ without any
    # template changes needed.
    root_path = os.environ.get("PLM_ROOT_PATH", "")

    uvicorn.run(app, host="0.0.0.0", port=port, root_path=root_path)
