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
  PLM_PORT            — listening port (optional, default 2026)
  PLM_TIMEZONE        — IANA zone for "today" / current week (optional, see plm.timeutil)
"""

# ── 1. Imports ──────────────────────────────────────────────────────────────
import asyncio
import hmac
import os
import re
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast

import markdown as _md
import uvicorn
from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import Response
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from plm.models.card import CardLog, KanbanCard, normalize_tags
from plm.models.inbox import InboxNote
from plm.models.planning import Day, TimeBlock, WeeklyPlan
from plm.models.profile import BehavioralProfile, ProfileUpdate
from plm.models.project import Project
from plm.storage.store import JsonStore
from plm.timeutil import current_week, today_weekday

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

# Register a Jinja2 filter so templates can render markdown with {{ text | markdown | safe }}.
# _md.markdown() returns an HTML string; Jinja2's `safe` marks it trusted for output.
templates.env.filters["markdown"] = lambda text: _md.markdown(text or "")

# _waiting / _loop: used by the SSE endpoint (sub-chunk 8d).
# Each connected browser tab registers an asyncio.Event here; the watchdog
# handler calls _notify_all() from the background thread via call_soon_threadsafe.
_waiting: list[asyncio.Event] = []
_loop: asyncio.AbstractEventLoop | None = None


# ── 3b. Planning helpers ─────────────────────────────────────────────────────
# Ordered list used by planning routes and templates — single source of truth.
_DAYS: list[str] = [
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
]

# Pattern that a valid ISO week string must match (path-traversal guard for filenames).
_WEEK_RE = re.compile(r"^\d{4}-W\d{2}$")

# Compiled once at module level rather than inside each request handler.
_TIME_RE = re.compile(r"^\d{2}:\d{2}$")


def _parse_week_monday(week: str) -> datetime:
    """Parse an ISO week string like '2026-W10' into the Monday of that week.

    strptime with %G/%V/%u handles ISO week numbering correctly:
      %G — ISO year (may differ from calendar year at year boundaries)
      %V — ISO week number
      %u — ISO weekday (1 = Monday)
    """
    return datetime.strptime(f"{week}-1", "%G-W%V-%u")


def _week_offset(week: str, delta: int) -> str:
    """Return the ISO week string *delta* weeks before/after *week*."""
    target = _parse_week_monday(week) + timedelta(weeks=delta)
    cal = target.isocalendar()
    return f"{cal.year}-W{cal.week:02d}"


def _validate_week(week: str) -> bool:
    """True if *week* is a plausible YYYY-Www string (guards against path traversal)."""
    return bool(_WEEK_RE.match(week))


def _week_label(week: str) -> str:
    """Human-readable date range, e.g. 'Mar 2 – 8, 2026' or 'Dec 29 – Jan 4, 2026'."""
    monday = _parse_week_monday(week)
    sunday = monday + timedelta(days=6)
    # %-d removes the leading zero on Linux (glibc); acceptable since we target Pi
    if monday.month == sunday.month:
        return f"{monday.strftime('%b %-d')} – {sunday.strftime('%-d, %Y')}"
    elif monday.year == sunday.year:
        return f"{monday.strftime('%b %-d')} – {sunday.strftime('%b %-d, %Y')}"
    else:
        return f"{monday.strftime('%b %-d, %Y')} – {sunday.strftime('%b %-d, %Y')}"


# ── 3c. Calendar grid helpers ────────────────────────────────────────────────

# Predefined palette — 10 perceptually distinct colours chosen to look good on
# both white card backgrounds and dark nav.  Index is determined by hashing the
# project UUID so the same project always gets the same colour.
_PROJECT_COLORS: list[str] = [
    "#3b82f6",  # blue
    "#10b981",  # emerald
    "#f59e0b",  # amber
    "#ef4444",  # red
    "#8b5cf6",  # violet
    "#06b6d4",  # cyan
    "#f97316",  # orange
    "#84cc16",  # lime
    "#ec4899",  # pink
    "#6366f1",  # indigo
]

# Pixels per hour in the calendar grid.  Must match the CSS --hour-px variable
# in planning.html (1440 px total = 24 × 60).
_HOUR_PX = 60


def _project_color(project_id: str) -> str:
    """Deterministic colour for a project derived from its UUID bytes.

    sum(bytes) mod palette_length — stable across restarts, no external dep.
    UUIDs have enough byte variation that adjacent IDs rarely get the same colour.
    """
    return _PROJECT_COLORS[sum(project_id.encode()) % len(_PROJECT_COLORS)]


def _tag_color(tag: str) -> str:
    """Deterministic colour for a card tag, from the same palette as projects.

    Lowercased first so it matches the case-insensitive dedupe in
    normalize_tags. Summing the bytes (rather than a real hash) has a useful
    side effect: tags differing in one character, like v0.1 / v0.2 / v0.3,
    land on neighbouring — and therefore distinct — palette colours.
    """
    return _PROJECT_COLORS[sum(tag.lower().encode()) % len(_PROJECT_COLORS)]


templates.env.filters["tag_color"] = _tag_color


def _block_top(block: TimeBlock) -> int:
    """Top offset in pixels for a block in the 24-hour calendar grid."""
    h, m = map(int, block.start_time.split(":"))
    # With _HOUR_PX = 60, this simplifies to h*60 + m (one pixel per minute).
    return (h * 60 + m) * _HOUR_PX // 60


def _block_height(block: TimeBlock) -> int:
    """Height in pixels for a block.  Minimum 15 px so very short blocks stay legible."""
    sh, sm = map(int, block.start_time.split(":"))
    eh, em = map(int, block.end_time.split(":"))
    duration_min = (eh * 60 + em) - (sh * 60 + sm)
    return max(duration_min * _HOUR_PX // 60, 15)


def _overlap_ranges(blocks: list[TimeBlock]) -> list[tuple[int, int]]:
    """Return the merged (start_min, end_min) ranges where blocks overlap.

    blocks are assumed to be on the same day.  Overlaps are allowed (not
    prevented) — the calendar only shades these ranges so clashes are visible.

    Sweep in start order, tracking the furthest end seen so far ("reach").
    A block starting before reach intersects an earlier block, and its
    largest intersection is with whichever earlier block reaches furthest:
    [start, min(end, reach)].  That gives the union of all pairwise
    intersections without comparing every pair.
    """
    def to_min(t: str) -> int:
        h, m = map(int, t.split(":"))
        return h * 60 + m

    ranges: list[tuple[int, int]] = []
    reach = -1
    for b in sorted(blocks, key=lambda b: to_min(b.start_time)):
        start, end = to_min(b.start_time), to_min(b.end_time)
        if start < reach:
            seg = (start, min(end, reach))
            # Merge with the previous range when they touch, so three-way
            # clashes render as one continuous band.
            if ranges and seg[0] <= ranges[-1][1]:
                ranges[-1] = (ranges[-1][0], max(ranges[-1][1], seg[1]))
            else:
                ranges.append(seg)
        reach = max(reach, end)
    return ranges


def _planned_hours(blocks: list[TimeBlock]) -> dict[str, float]:
    """Return {project_id: total_planned_hours} for a list of time blocks."""
    totals: dict[str, float] = defaultdict(float)
    for b in blocks:
        sh, sm = map(int, b.start_time.split(":"))
        eh, em = map(int, b.end_time.split(":"))
        totals[b.project_id] += ((eh * 60 + em) - (sh * 60 + sm)) / 60
    return dict(totals)


# ── 4. Watchdog + lifespan ───────────────────────────────────────────────────

def _data_version() -> int:
    """Newest modification time (µs) across the data directory — files and dirs.

    Used to drop redundant SSE reloads. Each page embeds the version it was
    rendered with, and each reload message carries the version at send time;
    the browser only reloads if the message's version is newer.

    Why this is needed: watchdog's inotify backend occasionally reports an
    atomic-write rename ~0.5 s late (it holds the first half of a rename while
    waiting for its pair). By then the browser has already loaded the page
    that followed its own POST, so without a version check that page receives
    a reload for data it already shows — and loses its client-side state.

    Directory mtimes are included so deletions (which don't touch any
    remaining file) still bump the version. Microseconds rather than ns keep
    the value within JavaScript's safe-integer range.
    """
    newest = 0
    for dirpath, _dirnames, filenames in os.walk(store._root):
        for p in [dirpath, *(os.path.join(dirpath, f) for f in filenames)]:
            try:
                newest = max(newest, os.stat(p).st_mtime_ns)
            except FileNotFoundError:
                # A .tmp file can vanish between listing and stat (atomic write)
                pass
    return newest // 1000


# Exposed to templates so base.html can embed the version the page was
# rendered with. Evaluated at render time, i.e. after the route read its data.
templates.env.globals["data_version"] = _data_version


def _notify_all() -> None:
    """Set every waiting SSE client's asyncio.Event, triggering a reload send.

    Must be called from within the event loop (use call_soon_threadsafe when
    calling from the watchdog background thread).
    """
    for ev in _waiting:
        ev.set()


class _StoreWatcher(FileSystemEventHandler):
    """Watchdog handler: fires _notify_all() whenever any store file changes.

    Watchdog runs in its own background thread, so we must schedule
    _notify_all() onto the asyncio event loop via call_soon_threadsafe — direct
    asyncio calls from a non-loop thread are not safe.
    """

    def on_any_event(self, event: FileSystemEvent) -> None:
        # Ignore directory events — we care about file content changes only.
        if event.is_directory:
            return
        # Only react to write events (modified, created, moved, deleted).
        # "opened" and "closed_no_write" fire on reads too — the app reads
        # store files on every request, which would cause an infinite reload
        # loop: read → watchdog → reload → read → ...
        if event.event_type not in ("modified", "created", "moved", "deleted"):
            return
        # Whitelist: only PLM data files (.json) should trigger a reload.
        # All real PLM store files are .json; Syncthing housekeeping files are not.
        # For moved events, check dest_path — the store uses atomic writes
        # (os.replace("file.tmp", "file.json")), so src_path is the .tmp file
        # and dest_path is the final .json file.
        path = str(getattr(event, "dest_path", None) or event.src_path)
        if not path.endswith(".json"):
            return
        if _loop is not None:
            _loop.call_soon_threadsafe(_notify_all)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Start the watchdog observer on startup; stop it cleanly on shutdown."""
    global _loop
    _loop = asyncio.get_event_loop()

    observer = Observer()
    # Watch the entire data directory recursively so projects/, planning/,
    # inbox.json, and profile.json are all covered by one observer.
    observer.schedule(_StoreWatcher(), str(store._root), recursive=True)
    observer.start()

    yield

    observer.stop()
    observer.join()


# ── 5. App + middleware ──────────────────────────────────────────────────────
app = FastAPI(title="Personal Life Manager", lifespan=_lifespan)

# Serve static assets (logo, etc.) from the static/ directory next to this file.
_static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

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
    session_cookie="plm_session",
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
    return templates.TemplateResponse(request, template_name, ctx)


# ── 7a. Auth routes ──────────────────────────────────────────────────────────

@app.get("/manifest.json", name="manifest", include_in_schema=False)
async def manifest(request: Request) -> Response:
    """Render the PWA web app manifest as a Jinja2 template.

    Serving via a route (rather than as a static file) lets us use
    request.url_for() inside the template, so all icon and start_url paths
    are correct regardless of whether the app is mounted at / or /plm/.
    """
    content = templates.TemplateResponse("manifest.json", {"request": request})
    return Response(content.body, media_type="application/manifest+json")


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
    # Every tag used on this board, for the tag field's suggestions. Sorted
    # case-insensitively; dedupe keeps the first spelling seen.
    board_tags = normalize_tags(
        [tag for col in project.board.columns for card in col.cards for tag in card.tags]
    )
    board_tags.sort(key=str.lower)
    return _render(request, "board.html", {"project": project, "board_tags": board_tags})


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
    # Comma-separated; an empty field clears all tags
    tags: str = Form(""),
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
    # Trimming / dedupe happens in the model's validator
    card.tags = tags.split(",")
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
    # Sent by drag-and-drop (index within the target column after the move).
    # Omitted by the "Move to" dropdown, which appends to the end.
    position: int | None = Form(None),
    _: None = Depends(require_auth),
) -> Response:
    project = store.get_project(project_id)
    if project is None:
        _flash(request, "Project not found.", "error")
        return RedirectResponse(url=str(request.url_for("project_list")), status_code=303)

    try:
        project.board.move_card(card_id, target_col_id, position)
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
    # Target index after the move, as reported by column drag-and-drop
    position: int = Form(...),
    _: None = Depends(require_auth),
) -> Response:
    project = store.get_project(project_id)
    if project is None:
        _flash(request, "Project not found.", "error")
        return RedirectResponse(url=str(request.url_for("project_list")), status_code=303)

    try:
        project.board.move_column(col_id, position)
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


# ── 7d. Planning routes (8c) ─────────────────────────────────────────────────

@app.get("/planning", name="planning_page")
async def planning_page(
    request: Request,
    week: str | None = None,
    _: None = Depends(require_auth),
) -> Response:
    # Silently fall back to the current week for missing or malformed values —
    # bad ?week= params should show a usable page, not an error.
    if week is None or not _validate_week(week):
        week = current_week()

    # Return the saved plan or a transient empty one; we don't save empty plans
    # to disk so the planning directory stays clean until the user adds content.
    plan = store.get_plan(week) or WeeklyPlan(week=week)

    # Active projects populate the "Add block" dropdown; archived projects are
    # excluded from the dropdown but still included in project_map so old blocks
    # that reference them can still display their names.
    all_projects = store.list_projects()
    active_projects = [p for p in all_projects if not p.archived]
    project_map = {p.id: p for p in all_projects}

    # ── Calendar grid data ───────────────────────────────────────────────────

    # Day column headers: name + date string (e.g. "Mon\nMar 2")
    monday = _parse_week_monday(week)
    day_headers = [
        (day, (monday + timedelta(days=i)).strftime("%b %-d"))
        for i, day in enumerate(_DAYS)
    ]

    # Enrich each block with pixel geometry and colour for the grid template.
    enriched_blocks: dict[str, list[dict]] = {day: [] for day in _DAYS}
    for block in sorted(plan.time_blocks, key=lambda b: b.start_time):
        if block.day not in enriched_blocks:
            continue
        proj = project_map.get(block.project_id)
        enriched_blocks[block.day].append({
            "block": block,
            "top": _block_top(block),
            "height": _block_height(block),
            "color": _project_color(block.project_id),
            "name": proj.name if proj else "(deleted project)",
        })

    # Pixel geometry of the shaded bands drawn where a day's blocks overlap.
    overlaps = {
        day: [
            {"top": s * _HOUR_PX // 60, "height": (e - s) * _HOUR_PX // 60}
            for s, e in _overlap_ranges([item["block"] for item in items])
        ]
        for day, items in enriched_blocks.items()
    }

    # Highlight today's column only when viewing the current week.
    # Local time (PLM_TIMEZONE), not UTC — blocks are in local time.
    this_week = current_week()
    today_day = today_weekday() if week == this_week else None

    # ── Bar chart data ───────────────────────────────────────────────────────

    ph = _planned_hours(plan.time_blocks)

    # Include all non-deleted projects that have blocks this week OR a target.
    chart_projects = [
        p for p in all_projects
        if ph.get(p.id, 0) > 0 or (not p.archived and (p.target_weekly_hours or 0) > 0)
    ]

    # Scale = largest single value across all planned hours and all targets,
    # so every bar uses the same pixel-per-hour ratio (as requested).
    scale_values = [ph.get(p.id, 0) for p in chart_projects]
    scale_values += [p.target_weekly_hours for p in chart_projects if p.target_weekly_hours]
    max_scale = max(scale_values) if scale_values else 1.0

    chart_rows = sorted(
        [
            {
                "name": p.name,
                "color": _project_color(p.id),
                "planned": ph.get(p.id, 0),
                "target": p.target_weekly_hours,
                # Percentages drive CSS widths; kept to 1 decimal to avoid
                # floating-point noise in the rendered HTML.
                "fill_pct": round(ph.get(p.id, 0) / max_scale * 100, 1),
                "target_pct": (
                    round(p.target_weekly_hours / max_scale * 100, 1)
                    if p.target_weekly_hours else None
                ),
            }
            for p in chart_projects
        ],
        # cast: Pylance widens dict values to str|float|None; "planned" is always float
        key=lambda r: cast(float, r["planned"]),
        reverse=True,
    )

    # Colour lookup used by the project palette chips in the template.
    # Includes all projects (not just active) so archived-project blocks still
    # get the right colour when shown in enriched_blocks.
    project_colors = {p.id: _project_color(p.id) for p in all_projects}

    return _render(request, "planning.html", {
        "week": week,
        "week_label": _week_label(week),
        "current_week": this_week,
        "prev_week": _week_offset(week, -1),
        "next_week": _week_offset(week, 1),
        "plan": plan,
        "active_projects": active_projects,
        "project_map": project_map,
        "project_colors": project_colors,
        "days": _DAYS,
        # Calendar grid
        "day_headers": day_headers,
        "enriched_blocks": enriched_blocks,
        "overlaps": overlaps,
        "today_day": today_day,
        "hour_px": _HOUR_PX,
        # Bar chart
        "chart_rows": chart_rows,
    })


@app.post("/planning/blocks", name="add_block")
async def add_block(
    request: Request,
    week: str = Form(...),
    project_id: str = Form(...),
    day: str = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
    notes: str = Form(""),
    _: None = Depends(require_auth),
) -> Response:
    planning_url = str(request.url_for("planning_page"))

    if not _validate_week(week):
        _flash(request, "Invalid week.", "error")
        return RedirectResponse(url=planning_url, status_code=303)

    week_url = f"{planning_url}?week={week}"

    if store.get_project(project_id) is None:
        _flash(request, "Project not found.", "error")
        return RedirectResponse(url=week_url, status_code=303)

    if day not in set(_DAYS):
        _flash(request, "Invalid day.", "error")
        return RedirectResponse(url=week_url, status_code=303)

    # HTML <input type="time"> sends "HH:MM"; validate here as a server-side guard
    # in case someone posts directly without going through the browser form.
    if not _TIME_RE.match(start_time) or not _TIME_RE.match(end_time):
        _flash(request, "Times must be in HH:MM format.", "error")
        return RedirectResponse(url=week_url, status_code=303)

    # Midnight-spanning blocks are not supported (matches MCP server invariant).
    if end_time <= start_time:
        _flash(request, "End time must be after start time.", "error")
        return RedirectResponse(url=week_url, status_code=303)

    plan = store.get_plan(week) or WeeklyPlan(week=week)
    plan.time_blocks.append(TimeBlock(
        project_id=project_id,
        # cast is safe: validated against _DAYS above; Pylance can't narrow str→Literal
        day=cast("Day", day),
        start_time=start_time,
        end_time=end_time,
        notes=notes.strip(),
    ))
    plan.updated_at = datetime.now(timezone.utc)
    store.save_plan(plan)
    return RedirectResponse(url=week_url, status_code=303)


@app.post("/planning/blocks/{block_id}/delete", name="delete_block")
async def delete_block(
    block_id: str,
    request: Request,
    week: str = Form(...),
    _: None = Depends(require_auth),
) -> Response:
    planning_url = str(request.url_for("planning_page"))

    if not _validate_week(week):
        _flash(request, "Invalid week.", "error")
        return RedirectResponse(url=planning_url, status_code=303)

    week_url = f"{planning_url}?week={week}"

    plan = store.get_plan(week)
    if plan is None:
        _flash(request, "No plan found for this week.", "error")
        return RedirectResponse(url=week_url, status_code=303)

    original_len = len(plan.time_blocks)
    plan.time_blocks = [b for b in plan.time_blocks if b.id != block_id]
    if len(plan.time_blocks) == original_len:
        _flash(request, "Time block not found.", "error")
    else:
        plan.updated_at = datetime.now(timezone.utc)
        store.save_plan(plan)

    return RedirectResponse(url=week_url, status_code=303)


@app.post("/planning/blocks/{block_id}/move", name="move_block")
async def move_block(
    block_id: str,
    request: Request,
    week: str = Form(...),
    day: str = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
    _: None = Depends(require_auth),
) -> Response:
    """Reposition a time block to a new day/time slot.

    Called by the interactive calendar grid when the user drags a block.
    The block's project and notes are preserved; only day/start/end change.
    """
    planning_url = str(request.url_for("planning_page"))

    if not _validate_week(week):
        _flash(request, "Invalid week.", "error")
        return RedirectResponse(url=planning_url, status_code=303)

    week_url = f"{planning_url}?week={week}"

    if day not in set(_DAYS):
        _flash(request, "Invalid day.", "error")
        return RedirectResponse(url=week_url, status_code=303)

    if not _TIME_RE.match(start_time) or not _TIME_RE.match(end_time):
        _flash(request, "Times must be in HH:MM format.", "error")
        return RedirectResponse(url=week_url, status_code=303)

    if end_time <= start_time:
        _flash(request, "End time must be after start time.", "error")
        return RedirectResponse(url=week_url, status_code=303)

    plan = store.get_plan(week)
    if plan is None:
        _flash(request, "No plan found for this week.", "error")
        return RedirectResponse(url=week_url, status_code=303)

    block = next((b for b in plan.time_blocks if b.id == block_id), None)
    if block is None:
        _flash(request, "Time block not found.", "error")
        return RedirectResponse(url=week_url, status_code=303)

    block.day = cast("Day", day)
    block.start_time = start_time
    block.end_time = end_time
    plan.updated_at = datetime.now(timezone.utc)
    store.save_plan(plan)
    return RedirectResponse(url=week_url, status_code=303)


@app.post("/planning/blocks/{block_id}/update", name="update_block")
async def update_block(
    block_id: str,
    request: Request,
    week: str = Form(...),
    day: str = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
    notes: str = Form(""),
    _: None = Depends(require_auth),
) -> Response:
    """Edit an existing time block's day, times, and notes.

    Supersedes move_block for form-based edits; move_block is still used by the
    drag-and-drop gesture.  The block's project is intentionally not editable
    here — changing the project would require re-selecting from the palette,
    which is a different UX flow.
    """
    planning_url = str(request.url_for("planning_page"))

    if not _validate_week(week):
        _flash(request, "Invalid week.", "error")
        return RedirectResponse(url=planning_url, status_code=303)

    week_url = f"{planning_url}?week={week}"

    if day not in set(_DAYS):
        _flash(request, "Invalid day.", "error")
        return RedirectResponse(url=week_url, status_code=303)

    if not _TIME_RE.match(start_time) or not _TIME_RE.match(end_time):
        _flash(request, "Times must be in HH:MM format.", "error")
        return RedirectResponse(url=week_url, status_code=303)

    if end_time <= start_time:
        _flash(request, "End time must be after start time.", "error")
        return RedirectResponse(url=week_url, status_code=303)

    plan = store.get_plan(week)
    if plan is None:
        _flash(request, "No plan found for this week.", "error")
        return RedirectResponse(url=week_url, status_code=303)

    block = next((b for b in plan.time_blocks if b.id == block_id), None)
    if block is None:
        _flash(request, "Time block not found.", "error")
        return RedirectResponse(url=week_url, status_code=303)

    block.day        = cast("Day", day)
    block.start_time = start_time
    block.end_time   = end_time
    block.notes      = notes.strip()
    plan.updated_at  = datetime.now(timezone.utc)
    store.save_plan(plan)
    return RedirectResponse(url=week_url, status_code=303)


@app.post("/planning/blocks/{block_id}/duplicate", name="duplicate_block")
async def duplicate_block(
    block_id: str,
    request: Request,
    week: str = Form(...),
    _: None = Depends(require_auth),
) -> Response:
    """Duplicate a time block within the same week.

    Creates an independent copy with a fresh UUID so the original and copy
    can be moved or deleted separately.  The copy lands on the same
    day/time as the source — the user can then drag it to a new slot.
    """
    planning_url = str(request.url_for("planning_page"))

    if not _validate_week(week):
        _flash(request, "Invalid week.", "error")
        return RedirectResponse(url=planning_url, status_code=303)

    week_url = f"{planning_url}?week={week}"

    plan = store.get_plan(week)
    if plan is None:
        _flash(request, "No plan found for this week.", "error")
        return RedirectResponse(url=week_url, status_code=303)

    block = next((b for b in plan.time_blocks if b.id == block_id), None)
    if block is None:
        _flash(request, "Time block not found.", "error")
        return RedirectResponse(url=week_url, status_code=303)

    # TimeBlock() auto-generates a fresh UUID for the copy's id field
    copy = TimeBlock(
        project_id=block.project_id,
        day=block.day,
        start_time=block.start_time,
        end_time=block.end_time,
        notes=block.notes,
    )
    plan.time_blocks.append(copy)
    plan.updated_at = datetime.now(timezone.utc)
    store.save_plan(plan)
    return RedirectResponse(url=week_url, status_code=303)


# Latest minute a block may end at — the calendar never submits "24:00"
# (see toTime() in planning.html), so a batch move must not produce it either.
_LAST_MINUTE = 23 * 60 + 59


@app.post("/planning/blocks/batch", name="batch_blocks")
async def batch_blocks(
    request: Request,
    week: str = Form(""),
    action: str = Form(""),
    block_ids: list[str] = Form([]),
    # Only used by action=move: the same shift is applied to every block so the
    # group keeps its internal spacing.  Offsets (not absolute positions) mean
    # the client cannot accidentally send inconsistent per-block targets.
    day_offset: int = Form(0),
    minute_offset: int = Form(0),
    _: None = Depends(require_auth),
) -> Response:
    """Apply one action to several time blocks at once: delete, duplicate, move.

    Called with fetch() by the multi-select UI rather than a form submit, so it
    answers with JSON instead of a redirect:
      - success → {"ok": true, "selected": [ids]} — the blocks the page should
        show as selected after reloading (copies after duplicate, the moved
        blocks after move, nothing after delete);
      - failure → 400 {"ok": false, "error": msg}, with the same message also
        flashed so it shows after the client's reload like any other error.

    All-or-nothing: every id must exist and a move must keep every block inside
    the week and the day, otherwise nothing is saved.  One save per batch means
    one file write and therefore one live-reload event.
    """
    def fail(message: str) -> JSONResponse:
        _flash(request, message, "error")
        return JSONResponse({"ok": False, "error": message}, status_code=400)

    if not _validate_week(week):
        return fail("Invalid week.")
    if action not in ("delete", "duplicate", "move"): # TODO: copy to next week stuff later maybe
        return fail("Unknown batch action.")

    # dict.fromkeys dedupes while keeping the client's order
    ids = list(dict.fromkeys(block_ids))
    if not ids:
        return fail("No time blocks selected.")

    plan = store.get_plan(week)
    if plan is None:
        return fail("No plan found for this week.")

    by_id = {b.id: b for b in plan.time_blocks}
    missing = [i for i in ids if i not in by_id]
    if missing:
        # Most likely a stale page (block removed elsewhere, e.g. via MCP)
        return fail(f"{len(missing)} selected time block(s) no longer exist.")
    selected_blocks = [by_id[i] for i in ids]

    def to_min(t: str) -> int:
        h, m = map(int, t.split(":"))
        return h * 60 + m

    def to_time(total: int) -> str:
        return f"{total // 60:02d}:{total % 60:02d}"

    if action == "delete":
        doomed = set(ids)
        plan.time_blocks = [b for b in plan.time_blocks if b.id not in doomed]
        selected: list[str] = []

    elif action == "duplicate":
        # Same semantics as duplicate_block: copies land on the originals' slots
        copies = [
            TimeBlock(
                project_id=b.project_id,
                day=b.day,
                start_time=b.start_time,
                end_time=b.end_time,
                notes=b.notes,
            )
            for b in selected_blocks
        ]
        plan.time_blocks.extend(copies)
        selected = [c.id for c in copies]

    else:  # move
        # Validate every block's target before touching any of them
        targets = []
        for b in selected_blocks:
            day_idx = _DAYS.index(b.day) + day_offset
            start = to_min(b.start_time) + minute_offset
            end = to_min(b.end_time) + minute_offset
            if not 0 <= day_idx < len(_DAYS) or start < 0 or end > _LAST_MINUTE:
                return fail("Move would take blocks outside the week or past midnight.")
            targets.append((b, _DAYS[day_idx], start, end))
        for b, day, start, end in targets:
            b.day = cast("Day", day)
            b.start_time = to_time(start)
            b.end_time = to_time(end)
        selected = ids

    plan.updated_at = datetime.now(timezone.utc)
    store.save_plan(plan)
    return JSONResponse({"ok": True, "selected": selected})


@app.post("/planning/notes", name="save_plan_notes")
async def save_plan_notes(
    request: Request,
    week: str = Form(...),
    session_notes: str = Form(""),
    constraints: str = Form(""),
    _: None = Depends(require_auth),
) -> Response:
    planning_url = str(request.url_for("planning_page"))

    if not _validate_week(week):
        _flash(request, "Invalid week.", "error")
        return RedirectResponse(url=planning_url, status_code=303)

    week_url = f"{planning_url}?week={week}"

    # Create the plan if it doesn't exist yet — saving notes is a valid reason
    # to materialise a plan file even with no time blocks.
    plan = store.get_plan(week) or WeeklyPlan(week=week)
    plan.session_notes = session_notes.strip()
    plan.constraints = constraints.strip()
    plan.updated_at = datetime.now(timezone.utc)
    store.save_plan(plan)
    _flash(request, "Notes saved.", "success")
    return RedirectResponse(url=week_url, status_code=303)


# ── 7e. Inbox routes (8d) ────────────────────────────────────────────────────

@app.get("/inbox", name="inbox_page")
async def inbox_page(
    request: Request,
    _: None = Depends(require_auth),
) -> Response:
    notes = store.get_inbox()
    # Split into unaddressed and addressed; within each group newest-first.
    unaddressed = sorted(
        [n for n in notes if not n.addressed],
        key=lambda n: n.created_at,
        reverse=True,
    )
    addressed = sorted(
        [n for n in notes if n.addressed],
        key=lambda n: n.addressed_at or n.created_at,
        reverse=True,
    )
    return _render(request, "inbox.html", {
        "unaddressed": unaddressed,
        "addressed": addressed,
    })


@app.post("/inbox/notes", name="inbox_add_note")
async def inbox_add_note(
    request: Request,
    content: str = Form(""),
    _: None = Depends(require_auth),
) -> Response:
    content_stripped = content.strip()
    if not content_stripped:
        _flash(request, "Note content cannot be empty.", "error")
        return RedirectResponse(url=str(request.url_for("inbox_page")), status_code=303)

    store.add_inbox_note(InboxNote(content=content_stripped))
    return RedirectResponse(url=str(request.url_for("inbox_page")), status_code=303)


@app.post("/inbox/notes/{note_id}/address", name="inbox_address_note")
async def inbox_address_note(
    note_id: str,
    request: Request,
    _: None = Depends(require_auth),
) -> Response:
    """Toggle a note between addressed and unaddressed."""
    notes = store.get_inbox()
    note = next((n for n in notes if n.id == note_id), None)
    if note is None:
        _flash(request, "Note not found.", "error")
        return RedirectResponse(url=str(request.url_for("inbox_page")), status_code=303)

    note.addressed = not note.addressed
    # Record when it was addressed; clear the timestamp when un-addressing so the
    # sort order for re-addressed notes is based on the new timestamp.
    note.addressed_at = datetime.now(timezone.utc) if note.addressed else None
    store.save_inbox(notes)
    return RedirectResponse(url=str(request.url_for("inbox_page")), status_code=303)


@app.post("/inbox/notes/{note_id}/delete", name="inbox_delete_note")
async def inbox_delete_note(
    note_id: str,
    request: Request,
    _: None = Depends(require_auth),
) -> Response:
    notes = store.get_inbox()
    original_count = len(notes)
    notes = [n for n in notes if n.id != note_id]
    if len(notes) == original_count:
        _flash(request, "Note not found.", "error")
        return RedirectResponse(url=str(request.url_for("inbox_page")), status_code=303)

    store.save_inbox(notes)
    return RedirectResponse(url=str(request.url_for("inbox_page")), status_code=303)


# ── 7f. Profile routes (8d) ──────────────────────────────────────────────────

@app.get("/profile", name="profile_page")
async def profile_page(
    request: Request,
    _: None = Depends(require_auth),
) -> Response:
    profile = store.get_profile()
    # History newest-first for display; the model stores it append-only (oldest-first).
    history = list(reversed(profile.history))
    return _render(request, "profile.html", {"profile": profile, "history": history})


@app.post("/profile", name="profile_update")
async def profile_update(
    request: Request,
    content: str = Form(""),
    change_summary: str = Form(""),
    _: None = Depends(require_auth),
) -> Response:
    """Save a new profile content string.

    If the user provides a change_summary, a ProfileUpdate history entry is
    appended so the evolution of the profile is traceable.  The summary is
    optional — a direct edit without explanation is still valid.
    """
    profile = store.get_profile()
    profile.content = content.strip()
    profile.last_updated = datetime.now(timezone.utc)

    summary_stripped = change_summary.strip()
    if summary_stripped:
        profile.history.append(ProfileUpdate(summary=summary_stripped))

    store.save_profile(profile)
    _flash(request, "Profile saved.", "success")
    return RedirectResponse(url=str(request.url_for("profile_page")), status_code=303)


# ── 7g. SSE live-refresh endpoint (8d) ──────────────────────────────────────

@app.get("/events", name="sse_events")
async def sse_events(
    request: Request,
    _: None = Depends(require_auth),
) -> Response:
    """Server-Sent Events endpoint — pushes a 'reload' message whenever any
    store file changes.

    Flow:
      1. Browser tab connects to GET /events (persistent HTTP connection).
      2. This handler registers an asyncio.Event in _waiting and then loops,
         waiting for the event to be set.
      3. When watchdog detects a file change, _notify_all() sets all events.
      4. The generator sends 'data: reload <version>\\n\\n' and clears its event.
      5. The browser reloads (or shows a banner) only if <version> is newer
         than the one its page was rendered with — see _data_version().

    A 30-second heartbeat comment keeps the connection alive through proxies
    that would otherwise close idle connections.
    """
    async def event_stream():
        ev = asyncio.Event()
        _waiting.append(ev)
        try:
            while True:
                # Give up if the client has gone away
                if await request.is_disconnected():
                    break
                try:
                    await asyncio.wait_for(ev.wait(), timeout=30.0)
                    ev.clear()
                    # Version read at send time, so a late watchdog event for a
                    # change the page already shows carries no newer version
                    yield f"data: reload {_data_version()}\n\n"
                except asyncio.TimeoutError:
                    # SSE comment — keeps the TCP connection alive; browsers ignore it
                    yield ": heartbeat\n\n"
        finally:
            # Always deregister so the list doesn't grow with stale events
            _waiting.remove(ev)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Tells Nginx / Caddy not to buffer SSE responses — without this,
            # events are held until the proxy's buffer fills, breaking live reload.
            "X-Accel-Buffering": "no",
        },
    )


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

    port = int(os.environ.get("PLM_PORT", "2026"))

    # root_path tells uvicorn (and FastAPI) the URL prefix under which the app
    # is mounted when behind a reverse proxy like Caddy at /plm/.
    # FastAPI propagates it into request.url_for() automatically, so all
    # template hrefs and redirects work correctly at / or /plm/ without any
    # template changes needed.
    root_path = os.environ.get("PLM_ROOT_PATH", "")

    # proxy_headers=True tells uvicorn to trust X-Forwarded-Proto from a reverse
    # proxy (e.g. Caddy).  Without this, uvicorn sees the Caddy→app hop as plain
    # HTTP and generates http:// URLs, causing browser "insecure form" warnings
    # even though the browser↔Caddy connection is HTTPS.
    # forwarded_allow_ips="*" is needed because the request arrives from the Docker
    # bridge gateway (172.17.0.1), not 127.0.0.1 — uvicorn's default trusted address.
    uvicorn.run(app, host="0.0.0.0", port=port, root_path=root_path,
                proxy_headers=True, forwarded_allow_ips="*")
