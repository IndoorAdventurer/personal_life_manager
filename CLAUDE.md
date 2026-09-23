# CLAUDE.md — Personal Life Manager

## What this project is
A Python MCP server + lightweight web UI (FastAPI + Jinja2) that lets Claude act as a
personal productivity partner. Combines Kanban boards, weekly time-block planning,
weekly review sessions, a behavioral profile, and a quick-capture inbox.

See `README.md` for a full feature overview and quick-start instructions.

---

## Onboarding for new contributors (human or AI)

This project was built entirely through vibe-coding sessions with Claude Code — the
human author reviewed and committed each chunk, but wrote essentially no code directly.
If you want to extend it, the same workflow works well:

1. Read this file and `README.md` to understand what exists.
2. Read the relevant source files before proposing changes (models, storage, mcp_server, web).
3. Make one focused change at a time, run the test suite, then review.
4. The existing code is heavily commented — follow the same style.

### Key files to read first
| File | Why |
|---|---|
| `src/plm/models/` | All Pydantic v2 data models — start here to understand the data shape |
| `src/plm/storage/store.py` | `JsonStore` — the only persistence layer |
| `src/plm/mcp_server/server.py` | All 34 MCP tools |
| `src/plm/web/app.py` | All FastAPI routes |
| `src/plm/web/templates/base.html` | Shared layout, styles, and SSE live-reload script |

---

## Project structure
```
src/plm/
├── models/       # Pydantic v2 data models
│   ├── card.py       # KanbanCard, CardLog
│   ├── column.py     # KanbanColumn
│   ├── board.py      # KanbanBoard (with column-management methods + WIP invariant)
│   ├── project.py    # Project
│   ├── planning.py   # WeeklyPlan, TimeBlock, Day enum
│   ├── inbox.py      # InboxNote
│   └── profile.py    # BehavioralProfile, ProfileUpdate
├── storage/      # JsonStore — generic atomic JSON file CRUD
│   └── store.py
├── mcp_server/   # FastMCP server (34 tools; stdio + HTTP entry points)
│   └── server.py
└── web/          # FastAPI app + Jinja2 templates
    ├── app.py
    └── templates/
        ├── base.html
        ├── login.html
        ├── projects.html
        ├── board.html
        ├── planning.html
        ├── inbox.html
        └── profile.html
tests/            # 347-test pytest suite
resources/        # Claude prompts: first-time setup guide + reusable skills
pyproject.toml    # packaging + dependencies
```

---

## Running things

```bash
# Install (editable, with dev deps)
pip install -e ".[dev]"
# or: uv pip install -e ".[dev]"

# Required environment variables
export PLM_PASSWORD="your-password"
export PLM_SESSION_SECRET="a-long-random-string"

# Run MCP server — stdio transport (local, no auth)
plm-mcp

# Run MCP server — Streamable HTTP transport (remote; OAuth via WorkOS AuthKit
# when WORKOS_AUTHKIT_DOMAIN + PLM_MCP_BASE_URL are set — see docs/pi-deployment.md § 6)
plm-mcp-http

# Run web UI (default port 2026)
plm-web

# Override data dir (useful for testing)
PLM_DATA_DIR=/tmp/plm-test plm-web

# Run tests
pytest -v
```

---

## Key design decisions

- **Storage**: one JSON file per project, one per week. Atomic writes via `.tmp` +
  `os.replace()` to avoid corruption.
- **Models**: Pydantic v2 throughout — free JSON serialisation + validation.
- **MCP**: standalone `fastmcp` package (`fastmcp.FastMCP`) — decorator-based, minimal
  boilerplate. One `mcp` instance serves both transports; auth (`AuthKitProvider`) is only
  constructed when the WorkOS env vars are set. Pinned to 3.1.1: 3.2.x validates the
  token audience (RFC 8707), which needs WorkOS Resource Indicators enabled first.
  Module-level `store` singleton; tests swap it via `srv.store = JsonStore(tmp_dir)`.
- **Deployment**: `plm` (web) and `plm-mcp` (HTTP MCP, Compose profile `mcp`) run as two
  Docker containers on the Pi, sharing the bind-mounted data dir, behind Caddy.
- **Web**: server-rendered Jinja2, no JS framework — keeps Pi deployment simple.
  SortableJS is vendored in `web/static/` for board drag-and-drop (no CDN).
- **Board invariant**: every `KanbanBoard` must have at least one WIP column.
  Enforced by a Pydantic `model_validator`.
- **Inbox**: stored as a single `inbox.json` list (not per-project files) — notes are
  global, not project-scoped.
- **Datetimes**: always `datetime.now(timezone.utc)` — timezone-aware throughout.
- **Live reload**: watchdog watches the data directory → asyncio Event → SSE broadcast
  → browser reloads (only if no input is focused, to avoid losing user input).
  Each message carries a data version (newest mtime in the data dir); the browser
  ignores it unless newer than the version its page was rendered with. This drops
  late watchdog echoes of the user's own POSTs (watchdog can report a rename ~0.5 s late).

---

## Dependencies
```
fastmcp==3.1.1
fastapi>=0.115.0
uvicorn[standard]>=0.32.0
jinja2>=3.1.0
pydantic>=2.0.0
python-multipart>=0.0.12
itsdangerous>=2.0
watchdog>=3.0.0
markdown>=3.0
tzdata
icalendar>=6.0

[dev]
pytest>=8.0.0
pytest-asyncio>=0.24.0
httpx>=0.27.0
```

---

## Data storage layout
```
~/.local/share/plm/          # override with PLM_DATA_DIR
├── projects/{id}.json
├── planning/{YYYY-Www}.json
├── inbox.json
└── profile.json
```

---

## Coding style conventions

- **Comments**: add a comment for every non-obvious decision — why a field is optional,
  why atomic writes, why a validator exists. Inline for short; block comment above
  function/class for bigger decisions.
- **Error messages**: MCP tools raise `ValueError` with a human-readable message.
  Web routes use flash messages stored in the session.
- **Helpers**: follow the `_require_project` / `_require_card` pattern in the MCP
  server — centralise repeated lookup + error logic.
- **Tests**: use `autouse=True` fixtures that swap `store` with a tmp-backed `JsonStore`.
  Use a sentinel timestamp (`datetime(2000, 1, 1, tzinfo=utc)`) for `updated_at`
  assertions to avoid false passes when code runs faster than clock resolution.
- **Forms**: use `Form("")` (not `Form(...)`) for user-typed name fields — FastAPI's
  422 fires before the handler for missing required fields; validate inside the handler
  instead and return a user-friendly flash error.

---

## Resources (Claude prompts)

The `resources/` directory contains two prompts:

| File | Purpose |
|---|---|
| `plm-init.md` | One-time setup: paste into a Claude Code session to add projects, create a General project, and build an initial behavioral profile |
| `weekly-review.md` | Reusable skill: symlink to `~/.claude/commands/` and invoke with `/weekly-review` to run a weekly review and planning session |

If a user mentions they haven't set up their projects or profile yet, point them to `resources/plm-init.md`.

---

## Post-MVP items (deferred, not forgotten)

See `memory/MEMORY.md` for the full list. Highlights:

- Mobile responsiveness audit (nav, Kanban scroll)
- Overlapping time-block prevention
- Multi-select time blocks (batch delete/move)
- Extract `_require_plan` / `_require_block` helpers in `app.py` (mirrors MCP pattern)
- Remove `move_block` route once drag-and-drop switches to `update_block`
