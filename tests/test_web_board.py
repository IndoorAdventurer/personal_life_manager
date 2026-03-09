"""
Tests for the Kanban board web routes (sub-chunk 8b).

Strategy
--------
Each test uses a TestClient backed by a temp-dir JsonStore, with the module-level
`store` and `_PLM_PASSWORD` singletons swapped via monkeypatch.  A shared `client`
fixture handles login so all tests start authenticated.

All POST routes return 303 redirects.  Tests assert the 303, then GET the board
to verify the resulting state — this mirrors how a real browser behaves.

TODO: these tests were not deeply reviewed by Vincent — worth a closer read at some point.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

import plm.web.app as app_module
from plm.models.card import KanbanCard
from plm.models.project import Project
from plm.storage.store import JsonStore

_TEST_PASSWORD = "test-password"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path: Path) -> JsonStore:
    """Blank JsonStore in a temp directory."""
    return JsonStore(data_dir=tmp_path)


@pytest.fixture
def client(store: JsonStore, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """
    An authenticated TestClient with an isolated store.

    monkeypatch swaps the module-level singletons so the routes hit the temp
    store and accept the test password.  TestClient used as a context manager
    ensures the ASGI lifespan runs (needed for SessionMiddleware).
    """
    monkeypatch.setattr(app_module, "store", store)
    monkeypatch.setattr(app_module, "_PLM_PASSWORD", _TEST_PASSWORD)

    with TestClient(app_module.app, raise_server_exceptions=True) as c:
        resp = c.post("/login", data={"password": _TEST_PASSWORD},
                      follow_redirects=True)
        assert resp.status_code == 200
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_project(store: JsonStore, name: str = "Test Project") -> Project:
    """Create and save a project, returning the Project object."""
    p = Project(name=name)
    store.save_project(p)
    return p


def _first_col_id(store: JsonStore, project_id: str) -> str:
    p = store.get_project(project_id)
    assert p is not None
    return p.board.columns[0].id


def _wip_col_id(store: JsonStore, project_id: str) -> str:
    p = store.get_project(project_id)
    assert p is not None
    return next(c.id for c in p.board.columns if c.is_wip)


def _add_card(store: JsonStore, project_id: str, col_id: str | None = None,
              name: str = "A card") -> str:
    """Add a card to the project and return its id."""
    p = store.get_project(project_id)
    assert p is not None
    if col_id is None:
        col_id = p.board.columns[0].id
    col = next(c for c in p.board.columns if c.id == col_id)
    card = KanbanCard(name=name)
    col.cards.append(card)
    store.save_project(p)
    return card.id


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------

def test_board_requires_auth(store: JsonStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unauthenticated GET /projects/{id} redirects to /login."""
    monkeypatch.setattr(app_module, "store", store)
    monkeypatch.setattr(app_module, "_PLM_PASSWORD", _TEST_PASSWORD)

    p = _make_project(store)
    with TestClient(app_module.app, raise_server_exceptions=True) as c:
        # No login — follow the redirect chain and expect to land on /login
        resp = c.get(f"/projects/{p.id}", follow_redirects=True)
        assert resp.url.path == "/login"


# ---------------------------------------------------------------------------
# Board view
# ---------------------------------------------------------------------------

def test_view_board(client: TestClient, store: JsonStore) -> None:
    """GET /projects/{id} renders the board with the project name and columns."""
    p = _make_project(store, "My Board")
    resp = client.get(f"/projects/{p.id}")
    assert resp.status_code == 200
    assert "My Board" in resp.text
    # Default board has four columns — all names should appear
    for col in p.board.columns:
        assert col.name in resp.text


def test_view_board_not_found(client: TestClient) -> None:
    """GET /projects/bad-id redirects to project list with an error flash."""
    resp = client.get("/projects/no-such-id", follow_redirects=False)
    assert resp.status_code == 303
    # TestClient returns an absolute URL in the Location header; check the path only
    assert resp.headers["location"].endswith(
        str(client.app.url_path_for("project_list"))
    )


# ---------------------------------------------------------------------------
# Card creation
# ---------------------------------------------------------------------------

def test_create_card(client: TestClient, store: JsonStore) -> None:
    """POST /projects/{id}/cards creates a card in the specified column."""
    p = _make_project(store)
    col_id = _first_col_id(store, p.id)

    resp = client.post(
        f"/projects/{p.id}/cards",
        data={"col_id": col_id, "name": "My new card"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    # Verify the card was persisted
    p_updated = store.get_project(p.id)
    assert p_updated is not None
    col = next(c for c in p_updated.board.columns if c.id == col_id)
    assert any(card.name == "My new card" for card in col.cards)


def test_create_card_strips_whitespace(client: TestClient, store: JsonStore) -> None:
    """Leading/trailing whitespace is stripped from the card name."""
    p = _make_project(store)
    col_id = _first_col_id(store, p.id)

    client.post(
        f"/projects/{p.id}/cards",
        data={"col_id": col_id, "name": "  Trimmed  "},
        follow_redirects=False,
    )
    p_updated = store.get_project(p.id)
    assert p_updated is not None
    col = next(c for c in p_updated.board.columns if c.id == col_id)
    assert any(card.name == "Trimmed" for card in col.cards)


def test_create_card_empty_name_flashes_error(client: TestClient, store: JsonStore) -> None:
    """Submitting a blank card name flashes an error and creates nothing."""
    p = _make_project(store)
    col_id = _first_col_id(store, p.id)

    resp = client.post(
        f"/projects/{p.id}/cards",
        data={"col_id": col_id, "name": "   "},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "cannot be empty" in resp.text.lower()

    # No card should have been created
    p_updated = store.get_project(p.id)
    assert p_updated is not None
    total_cards = sum(len(c.cards) for c in p_updated.board.columns)
    assert total_cards == 0


# ---------------------------------------------------------------------------
# Card editing
# ---------------------------------------------------------------------------

def test_edit_card(client: TestClient, store: JsonStore) -> None:
    """POST /projects/{id}/cards/{card_id}/edit updates card fields."""
    p = _make_project(store)
    card_id = _add_card(store, p.id, name="Old name")

    client.post(
        f"/projects/{p.id}/cards/{card_id}/edit",
        data={
            "name": "New name",
            "description": "Some details",
            "estimated_workload": "3h",
        },
        follow_redirects=False,
    )

    p_updated = store.get_project(p.id)
    assert p_updated is not None
    result = p_updated.board.find_card(card_id)
    assert result is not None
    _, card = result
    assert card.name == "New name"
    assert card.description == "Some details"
    assert card.estimated_workload == "3h"


def test_edit_card_clears_workload_when_blank(client: TestClient, store: JsonStore) -> None:
    """Submitting an empty estimated_workload clears the field (sets it to None)."""
    p = _make_project(store)
    card_id = _add_card(store, p.id)

    # First set a workload
    client.post(
        f"/projects/{p.id}/cards/{card_id}/edit",
        data={"name": "Card", "description": "", "estimated_workload": "2h"},
        follow_redirects=False,
    )
    # Then clear it
    client.post(
        f"/projects/{p.id}/cards/{card_id}/edit",
        data={"name": "Card", "description": "", "estimated_workload": ""},
        follow_redirects=False,
    )

    p_updated = store.get_project(p.id)
    assert p_updated is not None
    result = p_updated.board.find_card(card_id)
    assert result is not None
    _, card = result
    assert card.estimated_workload is None


# ---------------------------------------------------------------------------
# Card log
# ---------------------------------------------------------------------------

def test_add_card_log(client: TestClient, store: JsonStore) -> None:
    """POST /projects/{id}/cards/{card_id}/log appends a log entry."""
    p = _make_project(store)
    card_id = _add_card(store, p.id)

    client.post(
        f"/projects/{p.id}/cards/{card_id}/log",
        data={"message": "Did some work"},
        follow_redirects=False,
    )

    p_updated = store.get_project(p.id)
    assert p_updated is not None
    result = p_updated.board.find_card(card_id)
    assert result is not None
    _, card = result
    assert len(card.logs) == 1
    assert card.logs[0].message == "Did some work"


def test_add_card_log_blank_message_ignored(client: TestClient, store: JsonStore) -> None:
    """A blank log message is rejected with a flash error — no log entry is created."""
    p = _make_project(store)
    card_id = _add_card(store, p.id)

    client.post(
        f"/projects/{p.id}/cards/{card_id}/log",
        data={"message": "   "},
        follow_redirects=False,
    )

    p_updated = store.get_project(p.id)
    assert p_updated is not None
    result = p_updated.board.find_card(card_id)
    assert result is not None
    _, card = result
    assert len(card.logs) == 0


# ---------------------------------------------------------------------------
# Card move
# ---------------------------------------------------------------------------

def test_move_card(client: TestClient, store: JsonStore) -> None:
    """POST /projects/{id}/cards/{card_id}/move moves the card to another column."""
    p = _make_project(store)
    src_col_id = _first_col_id(store, p.id)
    card_id = _add_card(store, p.id, col_id=src_col_id)

    # Move to the WIP column (second column by default)
    wip_col_id = _wip_col_id(store, p.id)

    resp = client.post(
        f"/projects/{p.id}/cards/{card_id}/move",
        data={"target_col_id": wip_col_id},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    p_updated = store.get_project(p.id)
    assert p_updated is not None

    # Card should now be in the WIP column, not the source column
    src_col = next(c for c in p_updated.board.columns if c.id == src_col_id)
    wip_col = next(c for c in p_updated.board.columns if c.id == wip_col_id)
    assert not any(c.id == card_id for c in src_col.cards)
    assert any(c.id == card_id for c in wip_col.cards)


def test_move_card_bad_column_flashes_error(client: TestClient, store: JsonStore) -> None:
    """Moving to a non-existent column flashes an error instead of crashing."""
    p = _make_project(store)
    card_id = _add_card(store, p.id)

    resp = client.post(
        f"/projects/{p.id}/cards/{card_id}/move",
        data={"target_col_id": "no-such-column"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "not found" in resp.text.lower()


# ---------------------------------------------------------------------------
# Card deletion
# ---------------------------------------------------------------------------

def test_delete_card(client: TestClient, store: JsonStore) -> None:
    """POST /projects/{id}/cards/{card_id}/delete removes the card."""
    p = _make_project(store)
    card_id = _add_card(store, p.id, name="Doomed card")

    resp = client.post(
        f"/projects/{p.id}/cards/{card_id}/delete",
        follow_redirects=False,
    )
    assert resp.status_code == 303

    p_updated = store.get_project(p.id)
    assert p_updated is not None
    assert p_updated.board.find_card(card_id) is None


def test_delete_card_shows_success_flash(client: TestClient, store: JsonStore) -> None:
    """After deleting a card the board shows a success flash with the card name."""
    p = _make_project(store)
    card_id = _add_card(store, p.id, name="Gone card")

    resp = client.post(
        f"/projects/{p.id}/cards/{card_id}/delete",
        follow_redirects=True,
    )
    assert "Gone card" in resp.text
    assert "deleted" in resp.text.lower()


# ---------------------------------------------------------------------------
# Column management
# ---------------------------------------------------------------------------

def test_add_column(client: TestClient, store: JsonStore) -> None:
    """POST /projects/{id}/columns adds a new column to the board."""
    p = _make_project(store)
    original_count = len(p.board.columns)

    resp = client.post(
        f"/projects/{p.id}/columns",
        data={"name": "Review", "is_wip_raw": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    p_updated = store.get_project(p.id)
    assert p_updated is not None
    assert len(p_updated.board.columns) == original_count + 1
    assert any(c.name == "Review" for c in p_updated.board.columns)


def test_add_wip_column(client: TestClient, store: JsonStore) -> None:
    """Adding a column with is_wip_raw=on marks it as a WIP column."""
    p = _make_project(store)

    client.post(
        f"/projects/{p.id}/columns",
        data={"name": "Doing", "is_wip_raw": "on"},
        follow_redirects=False,
    )

    p_updated = store.get_project(p.id)
    assert p_updated is not None
    col = next((c for c in p_updated.board.columns if c.name == "Doing"), None)
    assert col is not None
    assert col.is_wip is True


def test_add_column_empty_name_flashes_error(client: TestClient, store: JsonStore) -> None:
    """Submitting a blank column name flashes an error."""
    p = _make_project(store)
    original_count = len(p.board.columns)

    resp = client.post(
        f"/projects/{p.id}/columns",
        data={"name": "  ", "is_wip_raw": ""},
        follow_redirects=True,
    )
    assert "cannot be empty" in resp.text.lower()

    p_updated = store.get_project(p.id)
    assert p_updated is not None
    assert len(p_updated.board.columns) == original_count


def test_rename_column(client: TestClient, store: JsonStore) -> None:
    """POST /projects/{id}/columns/{col_id}/rename renames the column."""
    p = _make_project(store)
    col_id = _first_col_id(store, p.id)

    client.post(
        f"/projects/{p.id}/columns/{col_id}/rename",
        data={"name": "Backlog"},
        follow_redirects=False,
    )

    p_updated = store.get_project(p.id)
    assert p_updated is not None
    col = next(c for c in p_updated.board.columns if c.id == col_id)
    assert col.name == "Backlog"


def test_delete_empty_column(client: TestClient, store: JsonStore) -> None:
    """POST /projects/{id}/columns/{col_id}/delete removes an empty column."""
    p = _make_project(store)
    # Add a fresh empty column so we don't accidentally delete a default one
    col = p.board.add_column("Temp")
    store.save_project(p)
    original_count = len(p.board.columns)

    resp = client.post(
        f"/projects/{p.id}/columns/{col.id}/delete",
        follow_redirects=False,
    )
    assert resp.status_code == 303

    p_updated = store.get_project(p.id)
    assert p_updated is not None
    assert len(p_updated.board.columns) == original_count - 1
    assert not any(c.id == col.id for c in p_updated.board.columns)


def test_delete_column_with_cards_flashes_error(client: TestClient, store: JsonStore) -> None:
    """Deleting a non-empty column flashes an error and leaves the column intact."""
    p = _make_project(store)
    col_id = _first_col_id(store, p.id)
    _add_card(store, p.id, col_id=col_id)
    original_count = len(p.board.columns)

    resp = client.post(
        f"/projects/{p.id}/columns/{col_id}/delete",
        follow_redirects=True,
    )
    assert resp.status_code == 200
    # The model's error message mentions "card(s)"
    assert "card" in resp.text.lower()

    p_updated = store.get_project(p.id)
    assert p_updated is not None
    assert len(p_updated.board.columns) == original_count


def test_delete_last_wip_column_flashes_error(client: TestClient, store: JsonStore) -> None:
    """Attempting to delete the sole WIP column is refused by the model."""
    p = _make_project(store)
    wip_col_id = _wip_col_id(store, p.id)
    original_count = len(p.board.columns)

    resp = client.post(
        f"/projects/{p.id}/columns/{wip_col_id}/delete",
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "wip" in resp.text.lower()

    p_updated = store.get_project(p.id)
    assert p_updated is not None
    assert len(p_updated.board.columns) == original_count
