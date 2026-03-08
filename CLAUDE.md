# CLAUDE.md — Personal Life Manager

## What this project is
A Python MCP server + lightweight web UI (FastAPI + Jinja2) that lets Claude act as a
personal productivity partner. Combines Kanban boards, weekly time-block planning,
weekly review sessions, a behavioral profile, and a quick-capture inbox.

Full plan: `~/.claude/projects/.../memory/implementation_plan.md`

## How to work on this project

### One chunk at a time
The implementation is split into 9 numbered chunks (see memory/MEMORY.md for the
checklist). **Complete one chunk, wait for the user to review and commit, then move on.**
Never start the next chunk without explicit "go ahead" from the user.

### Commenting style
Add a comment for every non-obvious decision — why a field is optional, why atomic
writes, why a validator exists, etc. Inline comments are fine for short explanations;
a block comment above a function/class for bigger decisions.

### No auto-committing
The user commits manually after reviewing each chunk.

## Project structure
```
src/plm/
├── models/       # Pydantic v2 data models (chunk 2)
├── storage/      # JsonStore — generic JSON file CRUD (chunk 4)
├── mcp_server/   # FastMCP tool definitions (chunk 6)
└── web/          # FastAPI app + Jinja2 templates (chunk 8)
tests/            # pytest test suite (chunks 3, 5, 7)
pyproject.toml    # packaging + dependencies (chunk 1)
```

## Running things

```bash
# Install (editable, with dev deps)
uv pip install -e ".[dev]"

# Run MCP server
plm-mcp

# Run web UI (default port 8000)
plm-web

# Override data dir (useful for testing)
PLM_DATA_DIR=/tmp/plm-test plm-web

# Run tests
pytest -v
```

## Key design decisions
- **Storage**: one JSON file per project, one per week. Atomic writes via `.tmp` +
  rename to avoid corruption.
- **Models**: Pydantic v2 throughout — free JSON serialisation + validation.
- **MCP**: FastMCP (`mcp.server.fastmcp.FastMCP`) — decorator-based, minimal boilerplate.
- **Web**: server-rendered Jinja2, no JS framework — keeps Pi deployment simple.
- **Board invariant**: every `KanbanBoard` must have at least one WIP column.
  Enforced by a Pydantic `model_validator`.
- **Inbox**: stored as a single `inbox.json` list (not per-project files) because
  notes are global, not project-scoped.
- **Datetimes**: always `datetime.now(timezone.utc)` — timezone-aware throughout.

## Dependencies
```
mcp>=1.0.0
fastapi>=0.115.0
uvicorn[standard]>=0.32.0
jinja2>=3.1.0
pydantic>=2.0.0
python-multipart>=0.0.12

[dev]
pytest>=8.0.0
pytest-asyncio>=0.24.0
httpx>=0.27.0
```

## Data storage location
`~/.local/share/plm/` (override with `PLM_DATA_DIR` env var)
