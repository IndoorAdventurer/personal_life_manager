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

from plm.models.card import CardLog, KanbanCard
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


@app.post("/projects/{project_id}/delete", name="delete_project")
async def delete_project(
    project_id: str,
    request: Request,
    _: None = Depends(require_auth),
) -> Response:
    project = store.get_project(project_id)
    if project is None:
        _flash(request, "Project not found.", "error")
        return RedirectResponse(url=str(request.url_for("project_list")), status_code=303)

    # Require the project to be archived first — a two-step process that makes
    # accidental permanent deletion much harder.
    if not project.archived:
        _flash(request, "Only archived projects can be deleted. Archive it first.", "error")
        return RedirectResponse(url=str(request.url_for("project_list")), status_code=303)

    store.delete_project(project_id)
    _flash(request, f"Project \"{project.name}\" permanently deleted.", "success")
    return RedirectResponse(url=str(request.url_for("project_list")), status_code=303)


# ── 7c. Kanban board (8b) ────────────────────────────────────────────────────

@app.get("/projects/{project_id}", name="project_detail")
async def project_detail(
    project_id: str,
    request: Request,
    _: None = Depends(require_auth),
) -> Response:
    project = store.get_project(project_id)
    if project is None:
        _flash(request, "Project not found.", "error")
        return RedirectResponse(url=str(request.url_for("project_list")), status_code=303)
    return _render(request, "board.html", {"project": project})


@app.post("/projects/{project_id}/cards", name="create_card")
async def create_card(
    project_id: str,
    request: Request,
    col_id: str = Form(...),
    # Form("") rather than Form(...): FastAPI validates before our handler runs,
    # so Form(...) with an empty submission returns a raw 422 JSON instead of
    # our friendly flash message.  Defaulting to "" lets our handler own the
    # validation and redirect back with a human-readable error.
    name: str = Form(""),
    _: None = Depends(require_auth),
) -> Response:
    project = store.get_project(project_id)
    if project is None:
        _flash(request, "Project not found.", "error")
        return RedirectResponse(url=str(request.url_for("project_list")), status_code=303)

    col = next((c for c in project.board.columns if c.id == col_id), None)
    if col is None:
        _flash(request, "Column not found.", "error")
        return RedirectResponse(
            url=str(request.url_for("project_detail", project_id=project_id)),
            status_code=303,
        )

    name_stripped = name.strip()
    if not name_stripped:
        _flash(request, "Card name cannot be empty.", "error")
        return RedirectResponse(
            url=str(request.url_for("project_detail", project_id=project_id)),
            status_code=303,
        )

    col.cards.append(KanbanCard(name=name_stripped))
    store.save_project(project)
    return RedirectResponse(
        url=str(request.url_for("project_detail", project_id=project_id)),
        status_code=303,
    )


@app.post("/projects/{project_id}/cards/{card_id}/edit", name="edit_card")
async def edit_card(
    project_id: str,
    card_id: str,
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    estimated_workload: str = Form(""),
    _: None = Depends(require_auth),
) -> Response:
    project = store.get_project(project_id)
    if project is None:
        _flash(request, "Project not found.", "error")
        return RedirectResponse(url=str(request.url_for("project_list")), status_code=303)

    result = project.board.find_card(card_id)
    if result is None:
        _flash(request, "Card not found.", "error")
        return RedirectResponse(
            url=str(request.url_for("project_detail", project_id=project_id)),
            status_code=303,
        )

    _col, card = result
    card.name = name.strip() or card.name   # keep old name if submitted blank
    card.description = description.strip()
    # Empty string → no workload estimate; otherwise store as-is (free text)
    card.estimated_workload = estimated_workload.strip() or None
    card.updated_at = datetime.now(timezone.utc)
    store.save_project(project)
    return RedirectResponse(
        url=str(request.url_for("project_detail", project_id=project_id)),
        status_code=303,
    )


@app.post("/projects/{project_id}/cards/{card_id}/log", name="add_card_log")
async def add_card_log(
    project_id: str,
    card_id: str,
    request: Request,
    message: str = Form(""),  # see create_card for why Form("") not Form(...)
    _: None = Depends(require_auth),
) -> Response:
    project = store.get_project(project_id)
    if project is None:
        _flash(request, "Project not found.", "error")
        return RedirectResponse(url=str(request.url_for("project_list")), status_code=303)

    result = project.board.find_card(card_id)
    if result is None:
        _flash(request, "Card not found.", "error")
        return RedirectResponse(
            url=str(request.url_for("project_detail", project_id=project_id)),
            status_code=303,
        )

    _col, card = result
    msg = message.strip()
    if not msg:
        _flash(request, "Log message cannot be empty.", "error")
        return RedirectResponse(
            url=str(request.url_for("project_detail", project_id=project_id)),
            status_code=303,
        )
    if msg:
        card.logs.append(CardLog(message=msg))
        card.updated_at = datetime.now(timezone.utc)
        store.save_project(project)
    return RedirectResponse(
        url=str(request.url_for("project_detail", project_id=project_id)),
        status_code=303,
    )


@app.post("/projects/{project_id}/cards/{card_id}/move", name="move_card")
async def move_card(
    project_id: str,
    card_id: str,
    request: Request,
    target_col_id: str = Form(...),
    _: None = Depends(require_auth),
) -> Response:
    project = store.get_project(project_id)
    if project is None:
        _flash(request, "Project not found.", "error")
        return RedirectResponse(url=str(request.url_for("project_list")), status_code=303)

    try:
        project.board.move_card(card_id, target_col_id)
    except ValueError as exc:
        _flash(request, str(exc), "error")
        return RedirectResponse(
            url=str(request.url_for("project_detail", project_id=project_id)),
            status_code=303,
        )

    store.save_project(project)
    return RedirectResponse(
        url=str(request.url_for("project_detail", project_id=project_id)),
        status_code=303,
    )


@app.post("/projects/{project_id}/cards/{card_id}/delete", name="delete_card")
async def delete_card(
    project_id: str,
    card_id: str,
    request: Request,
    _: None = Depends(require_auth),
) -> Response:
    project = store.get_project(project_id)
    if project is None:
        _flash(request, "Project not found.", "error")
        return RedirectResponse(url=str(request.url_for("project_list")), status_code=303)

    result = project.board.find_card(card_id)
    if result is None:
        _flash(request, "Card not found.", "error")
        return RedirectResponse(
            url=str(request.url_for("project_detail", project_id=project_id)),
            status_code=303,
        )

    col, card = result
    col.cards.remove(card)
    store.save_project(project)
    _flash(request, f"Card \"{card.name}\" deleted.", "success")
    return RedirectResponse(
        url=str(request.url_for("project_detail", project_id=project_id)),
        status_code=303,
    )


@app.post("/projects/{project_id}/columns", name="add_column")
async def add_column(
    project_id: str,
    request: Request,
    name: str = Form(""),   # see create_card for why Form("") not Form(...)
    is_wip_raw: str = Form(""),
    _: None = Depends(require_auth),
) -> Response:
    project = store.get_project(project_id)
    if project is None:
        _flash(request, "Project not found.", "error")
        return RedirectResponse(url=str(request.url_for("project_list")), status_code=303)

    name_stripped = name.strip()
    if not name_stripped:
        _flash(request, "Column name cannot be empty.", "error")
        return RedirectResponse(
            url=str(request.url_for("project_detail", project_id=project_id)),
            status_code=303,
        )

    # HTML checkboxes send "on" when checked; absent when unchecked
    is_wip = is_wip_raw == "on"
    project.board.add_column(name_stripped, is_wip=is_wip)
    store.save_project(project)
    return RedirectResponse(
        url=str(request.url_for("project_detail", project_id=project_id)),
        status_code=303,
    )


@app.post("/projects/{project_id}/columns/{col_id}/rename", name="update_column")
async def update_column(
    project_id: str,
    col_id: str,
    request: Request,
    name: str = Form(""),       # see create_card for why Form("") not Form(...)
    is_wip_raw: str = Form(""), # checkbox: "on" when checked, "" when unchecked
    _: None = Depends(require_auth),
) -> Response:
    project = store.get_project(project_id)
    if project is None:
        _flash(request, "Project not found.", "error")
        return RedirectResponse(url=str(request.url_for("project_list")), status_code=303)

    col = next((c for c in project.board.columns if c.id == col_id), None)
    if col is None:
        _flash(request, "Column not found.", "error")
        return RedirectResponse(
            url=str(request.url_for("project_detail", project_id=project_id)),
            status_code=303,
        )

    new_is_wip = is_wip_raw == "on"

    # Guard: unchecking the WIP flag on the last remaining WIP column would
    # violate the board invariant enforced by KanbanBoard.require_wip_column.
    if col.is_wip and not new_is_wip:
        wip_cols = [c for c in project.board.columns if c.is_wip]
        if len(wip_cols) == 1:
            _flash(request, "Cannot remove WIP status from the last WIP column.", "error")
            return RedirectResponse(
                url=str(request.url_for("project_detail", project_id=project_id)),
                status_code=303,
            )

    name_stripped = name.strip()
    if name_stripped:
        try:
            project.board.rename_column(col_id, name_stripped)
        except ValueError as exc:
            _flash(request, str(exc), "error")
            return RedirectResponse(
                url=str(request.url_for("project_detail", project_id=project_id)),
                status_code=303,
            )

    col.is_wip = new_is_wip
    store.save_project(project)
    return RedirectResponse(
        url=str(request.url_for("project_detail", project_id=project_id)),
        status_code=303,
    )


@app.post("/projects/{project_id}/columns/{col_id}/reorder", name="reorder_column")
async def reorder_column(
    project_id: str,
    col_id: str,
    request: Request,
    direction: str = Form(...),
    _: None = Depends(require_auth),
) -> Response:
    project = store.get_project(project_id)
    if project is None:
        _flash(request, "Project not found.", "error")
        return RedirectResponse(url=str(request.url_for("project_list")), status_code=303)

    cols = project.board.columns
    idx = next((i for i, c in enumerate(cols) if c.id == col_id), None)
    if idx is None:
        _flash(request, "Column not found.", "error")
        return RedirectResponse(
            url=str(request.url_for("project_detail", project_id=project_id)),
            status_code=303,
        )

    # Swap with neighbour; silently do nothing if already at the edge
    if direction == "left" and idx > 0:
        cols[idx], cols[idx - 1] = cols[idx - 1], cols[idx]
    elif direction == "right" and idx < len(cols) - 1:
        cols[idx], cols[idx + 1] = cols[idx + 1], cols[idx]

    store.save_project(project)
    return RedirectResponse(
        url=str(request.url_for("project_detail", project_id=project_id)),
        status_code=303,
    )


@app.post("/projects/{project_id}/columns/{col_id}/delete", name="delete_column")
async def delete_column(
    project_id: str,
    col_id: str,
    request: Request,
    _: None = Depends(require_auth),
) -> Response:
    project = store.get_project(project_id)
    if project is None:
        _flash(request, "Project not found.", "error")
        return RedirectResponse(url=str(request.url_for("project_list")), status_code=303)

    col = next((c for c in project.board.columns if c.id == col_id), None)
    col_name = col.name if col else "Column"

    try:
        # force=False: raises ValueError if the column still has cards,
        # so the user is prompted to move them first rather than silently losing data
        project.board.remove_column(col_id, force=False)
    except ValueError as exc:
        _flash(request, str(exc), "error")
        return RedirectResponse(
            url=str(request.url_for("project_detail", project_id=project_id)),
            status_code=303,
        )

    store.save_project(project)
    _flash(request, f"Column \"{col_name}\" deleted.", "success")
    return RedirectResponse(
        url=str(request.url_for("project_detail", project_id=project_id)),
        status_code=303,
    )


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
