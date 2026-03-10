# Personal Life Manager — A Vibe-Coding Experiment

A Python **MCP server** + lightweight **web UI** that turns Claude into a personal
productivity partner. Manage Kanban boards, plan your week on a 24-hour calendar,
capture quick thoughts in an inbox, and let Claude maintain a behavioral profile that
helps it coach you over time.

---

> **A vibe-coding experiment.** This project was built as an exercise in using LLMs
> as a coding assistant — learning to work effectively with AI tools by building
> something genuinely useful. The entire codebase was written by Claude
> ([claude-sonnet-4-6](https://claude.ai)) through conversational sessions in
> [Claude Code](https://claude.com/claude-code). The human author described what to
> build, reviewed each chunk, and committed the result — but wrote essentially zero
> code. Nine chunks, 326 tests, and a fully functional app — all through conversation.
> See [Extending with Claude Code](#extending-the-project-with-claude-code) if you
> want to continue in the same spirit.

---

## Intended workflow

This tool was built around a specific personal workflow. It may or may not fit yours,
but understanding it makes the design choices make sense.

The core idea is to combine **time-blocking** with **Kanban** in a way that removes
all daily decision fatigue. Time blocks answer *which project* to work on right now;
the Kanban board answers *what task* to pick up within that project. Together they
give you focus without tunnel vision — the calendar ensures you keep a healthy balance
across projects, while the board keeps individual work moving forward.

The idea is also that Claude acts as a **planning partner**, not just a task manager.
A typical week looks like this:

1. **Weekly review** — Start a Claude Code session and ask for a weekly review.
   Claude reads your Kanban boards, last week's time blocks, inbox notes, and
   behavioral profile, then helps you reflect on what got done, what didn't, and why.

2. **Weekly planning** — Claude helps you plan the coming week. You discuss priorities,
   Claude adds time blocks to the calendar, moves cards between columns, and clears
   the inbox. The web UI updates live as Claude works.

3. **During the week** — Open the calendar, see which project you should be working on now,
   open that project's board, and pick up the next card. No deliberation needed.
   Capture any stray thoughts into the inbox without breaking focus.

4. **Behavioral profile** — Over time, Claude builds and refines a profile of your
   working style: when you're most focused, what kinds of tasks you tend to underestimate,
   what helps you stay on track. It uses this in every planning session.

**The setup that makes this work:**
- `plm-web` runs on a Raspberry Pi — always-on, accessible from any device on the LAN
- `plm-mcp` runs on the laptop where you use Claude Code
- [Syncthing](https://syncthing.net) keeps the data directory in sync between laptop and Pi
- Claude edits data through the MCP tools; the Pi browser reflects changes in real time via SSE

You don't *need* this exact setup. The web UI is fully functional on its own — you can
use it without Claude at all. The MCP server is what makes the Claude collaboration
seamless.

---

## Features

| Area | What you get |
|---|---|
| **Kanban boards** | Per-project boards with configurable columns, WIP limits, drag-and-drop cards, and timestamped progress logs |
| **Weekly planner** | Scrollable 24-hour calendar grid, color-coded by project, with a bar chart showing planned vs. target hours |
| **Inbox** | Quick-capture notes that Claude can later triage, file, or act on |
| **Behavioral profile** | Free-form Markdown document Claude maintains about your working style, preferences, and habits — with a full audit trail |
| **Live sync** | The web UI auto-reloads whenever Claude (or anything else) changes a data file on disk |
| **MCP tools** | 33 tools that let Claude read and update everything — projects, cards, time blocks, inbox, and profile — without leaving the conversation |

---

## Architecture

```
┌─────────────────────┐        ┌──────────────────────────────────────┐
│   Claude (MCP)      │◄──────►│  plm-mcp  (FastMCP over stdio)       │
│   Claude Code       │        │  33 tools for all data operations    │
└─────────────────────┘        └──────────┬───────────────────────────┘
                                           │ shared JSON files on disk
┌─────────────────────┐        ┌──────────▼───────────────────────────┐
│   Browser           │◄──────►│  plm-web  (FastAPI + Jinja2)         │
│   (LAN / Pi)        │  HTML  │  Server-rendered UI, SSE live-reload │
└─────────────────────┘        └──────────────────────────────────────┘
```

Both processes read and write the same JSON files in `~/.local/share/plm/`.
The web UI uses watchdog to detect file changes and push a reload event to the
browser via Server-Sent Events.

`plm-web` provides the full feature set and works standalone — the MCP server is
optional, but it's what makes Claude collaboration smooth.

> **Platform note:** developed and tested on Linux. The core logic is
> platform-agnostic, but the deployment guide targets Linux (systemd, Raspberry Pi OS).
> A `Dockerfile` is included for older OS versions (e.g. Raspberry Pi OS Bullseye)
> that ship Python < 3.11 — see [docs/pi-deployment.md](docs/pi-deployment.md).

---

## Project structure

```
src/plm/
├── models/       # Pydantic v2 data models
├── storage/      # JsonStore — generic atomic JSON file CRUD
├── mcp_server/   # FastMCP server with 33 tools
└── web/          # FastAPI app + 7 Jinja2 templates
tests/            # 326-test pytest suite
pyproject.toml
CLAUDE.md         # Instructions for Claude Code (read this if you want to extend the project with Claude)
```

---

## Quick start

### Requirements

- Python 3.12+
- Claude Code (for MCP integration) or any MCP-compatible client

### Install

```bash
git clone https://github.com/your-username/personal_life_manager
cd personal_life_manager

# Standard pip install (editable, with dev deps)
pip install -e ".[dev]"

# Or if you use uv
uv pip install -e ".[dev]"
```

### Configure

`plm-web` requires two environment variables before it will start:

```bash
export PLM_PASSWORD="your-login-password"
export PLM_SESSION_SECRET="a-long-random-string"
```

For a quick local test this is enough. For a persistent deployment (e.g. on a Pi),
see [docs/pi-deployment.md](docs/pi-deployment.md) — it covers a proper env file,
a systemd user service, and a Caddy reverse proxy.

### Start the web UI

```bash
plm-web
# → http://localhost:8000
```

### Connect the MCP server to Claude Code

Add this to your Claude Code MCP config (usually `~/.claude/mcp.json` or via
`claude mcp add`):

```json
{
  "mcpServers": {
    "plm": {
      "command": "plm-mcp"
    }
  }
}
```

Then in any Claude Code conversation you can say things like:

- *"What's in my inbox?"*
- *"Create a new project called 'Home renovations' with a target of 5 hours per week."*
- *"Add a 2-hour block on Tuesday for the 'Side project' project."*
- *"Show me my WIP cards."*
- *"Update my behavioral profile to note that I prefer working in 90-minute focused sessions."*

### Run the tests

```bash
pytest -v
```

---

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `PLM_PASSWORD` | Yes | — | Login password for the web UI |
| `PLM_SESSION_SECRET` | Yes | — | Key used to sign session cookies |
| `PLM_DATA_DIR` | No | `~/.local/share/plm/` | Override data directory |
| `PLM_PORT` | No | `8000` | Web UI listening port |
| `PLM_ROOT_PATH` | No | `""` | Subpath prefix for reverse-proxy deployments (e.g. `/plm`) |

---

## Data storage

All data is stored as plain JSON files. No database required.

```
~/.local/share/plm/
├── projects/{project_id}.json    # One file per project (board + cards)
├── planning/{iso_week}.json      # One file per week (e.g. 2026-W10.json)
├── inbox.json                    # Global inbox notes list
└── profile.json                  # Behavioral profile + audit history
```

Writes are atomic: data is written to a `.tmp` file first, then renamed into place,
so a crash mid-write never corrupts existing data.

---

## Raspberry Pi deployment

See **[docs/pi-deployment.md](docs/pi-deployment.md)** for a full guide to running
`plm-web` as a persistent systemd service on a Raspberry Pi, including LAN access
and an optional Caddy reverse proxy.

---

## MCP tools reference

The MCP server exposes 33 tools across five areas:

| Area | Tools |
|---|---|
| **Projects** | `list_projects`, `create_project`, `get_project`, `update_project`, `archive_project` |
| **Columns** | `list_columns`, `add_column`, `rename_column`, `remove_column` |
| **Cards** | `list_cards`, `get_card`, `add_card`, `update_card`, `move_card`, `reorder_cards`, `append_card_log`, `delete_card` |
| **Planning** | `get_plan`, `create_plan`, `add_time_block`, `remove_time_block`, `update_time_block`, `get_weekly_hours_summary`, `get_wip_overview` |
| **Inbox** | `add_inbox_note`, `list_inbox_notes`, `mark_inbox_note_addressed`, `delete_inbox_note` |
| **Profile** | `get_behavioral_profile`, `get_profile_history`, `update_behavioral_profile`, `patch_behavioral_profile` |
| **Reviews** | `get_weekly_review_data` |

---

## Extending the project with Claude Code

`CLAUDE.md` (in the repo root) contains everything Claude Code needs to understand
the project and continue development. Open the project in Claude Code and Claude will
read it automatically. The implementation was done in 9 numbered "chunks" — you can
ask Claude to continue from any chunk or to add entirely new features.

---

## License

MIT
