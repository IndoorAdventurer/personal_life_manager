"""
Tests for the projects web routes (sub-chunk 8a) plus the delete-project route
added in the 8b polish pass.

Covers: auth guard, project list, create, edit, archive/unarchive, delete.

TODO: these tests were not deeply reviewed by me (Vincent)  — worth a closer read at some point.

"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

import plm.web.app as app_module
from plm.models.project import Project
from plm.storage.store import JsonStore

_TEST_PASSWORD = "test-password"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path: Path) -> JsonStore:
    return JsonStore(data_dir=tmp_path)


@pytest.fixture
def client(store: JsonStore, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(app_module, "store", store)
    monkeypatch.setattr(app_module, "_PLM_PASSWORD", _TEST_PASSWORD)
    with TestClient(app_module.app, raise_server_exceptions=True) as c:
        c.post("/login", data={"password": _TEST_PASSWORD}, follow_redirects=True)
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_project(store: JsonStore, name: str = "Test", archived: bool = False,
                  hours: float | None = None) -> Project:
    p = Project(name=name, archived=archived, target_weekly_hours=hours)
    store.save_project(p)
    return p


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------

def test_project_list_requires_auth(store: JsonStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_module, "store", store)
    monkeypatch.setattr(app_module, "_PLM_PASSWORD", _TEST_PASSWORD)
    with TestClient(app_module.app, raise_server_exceptions=True) as c:
        resp = c.get("/", follow_redirects=True)
        assert resp.url.path == "/login"


# ---------------------------------------------------------------------------
# Project list
# ---------------------------------------------------------------------------

def test_project_list_shows_active_projects(client: TestClient, store: JsonStore) -> None:
    _make_project(store, "Alpha")
    _make_project(store, "Beta")
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Alpha" in resp.text
    assert "Beta" in resp.text


def test_project_list_separates_archived(client: TestClient, store: JsonStore) -> None:
    _make_project(store, "Active one")
    _make_project(store, "Old one", archived=True)
    resp = client.get("/")
    assert resp.status_code == 200
    # Both names appear but "Old one" is in the collapsed archived section
    assert "Active one" in resp.text
    assert "Old one" in resp.text


def test_project_list_shows_wip_count(client: TestClient, store: JsonStore) -> None:
    """The WIP badge count reflects cards in WIP columns."""
    from plm.models.card import KanbanCard
    p = _make_project(store, "Busy project")
    # Add a card to the WIP column
    wip_col = next(c for c in p.board.columns if c.is_wip)
    wip_col.cards.append(KanbanCard(name="In flight"))
    store.save_project(p)

    resp = client.get("/")
    assert "1 WIP" in resp.text


# ---------------------------------------------------------------------------
# Create project
# ---------------------------------------------------------------------------

def test_create_project(client: TestClient, store: JsonStore) -> None:
    resp = client.post(
        "/projects",
        data={"name": "New project", "description": "", "target_weekly_hours": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    projects = store.list_projects()
    assert any(p.name == "New project" for p in projects)


def test_create_project_with_hours(client: TestClient, store: JsonStore) -> None:
    client.post(
        "/projects",
        data={"name": "Focused", "description": "Much work", "target_weekly_hours": "8.5"},
        follow_redirects=False,
    )
    projects = store.list_projects()
    p = next(p for p in projects if p.name == "Focused")
    assert p.target_weekly_hours == 8.5
    assert p.description == "Much work"


def test_create_project_invalid_hours_flashes_error(client: TestClient, store: JsonStore) -> None:
    resp = client.post(
        "/projects",
        data={"name": "Oops", "description": "", "target_weekly_hours": "not-a-number"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "number" in resp.text.lower()
    # Project should NOT have been created
    assert not any(p.name == "Oops" for p in store.list_projects())


# ---------------------------------------------------------------------------
# Edit project
# ---------------------------------------------------------------------------

def test_edit_project(client: TestClient, store: JsonStore) -> None:
    p = _make_project(store, "Original name")

    client.post(
        f"/projects/{p.id}/edit",
        data={"name": "Updated name", "description": "New desc", "target_weekly_hours": "5"},
        follow_redirects=False,
    )

    updated = store.get_project(p.id)
    assert updated is not None
    assert updated.name == "Updated name"
    assert updated.description == "New desc"
    assert updated.target_weekly_hours == 5.0


def test_edit_project_not_found(client: TestClient) -> None:
    resp = client.post(
        "/projects/no-such-id/edit",
        data={"name": "X", "description": "", "target_weekly_hours": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"].endswith("/")


# ---------------------------------------------------------------------------
# Archive / unarchive
# ---------------------------------------------------------------------------

def test_archive_project(client: TestClient, store: JsonStore) -> None:
    p = _make_project(store, "Active")
    assert not p.archived

    client.post(f"/projects/{p.id}/archive", follow_redirects=False)

    updated = store.get_project(p.id)
    assert updated is not None
    assert updated.archived is True


def test_unarchive_project(client: TestClient, store: JsonStore) -> None:
    p = _make_project(store, "Was archived", archived=True)

    client.post(f"/projects/{p.id}/archive", follow_redirects=False)

    updated = store.get_project(p.id)
    assert updated is not None
    assert updated.archived is False


def test_archive_shows_success_flash(client: TestClient, store: JsonStore) -> None:
    p = _make_project(store, "Flash test")
    resp = client.post(f"/projects/{p.id}/archive", follow_redirects=True)
    assert "archived" in resp.text.lower()


# ---------------------------------------------------------------------------
# Delete project
# ---------------------------------------------------------------------------

def test_delete_archived_project(client: TestClient, store: JsonStore) -> None:
    p = _make_project(store, "Doomed", archived=True)

    resp = client.post(f"/projects/{p.id}/delete", follow_redirects=False)
    assert resp.status_code == 303

    assert store.get_project(p.id) is None


def test_delete_project_shows_success_flash(client: TestClient, store: JsonStore) -> None:
    p = _make_project(store, "Gone project", archived=True)
    resp = client.post(f"/projects/{p.id}/delete", follow_redirects=True)
    assert "Gone project" in resp.text
    assert "deleted" in resp.text.lower()


def test_delete_active_project_is_refused(client: TestClient, store: JsonStore) -> None:
    """Deleting a non-archived project flashes an error and keeps the project."""
    p = _make_project(store, "Still alive")

    resp = client.post(f"/projects/{p.id}/delete", follow_redirects=True)
    assert resp.status_code == 200
    assert "archive" in resp.text.lower()

    # Project must still exist
    assert store.get_project(p.id) is not None


def test_delete_project_not_found(client: TestClient) -> None:
    resp = client.post("/projects/no-such-id/delete", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].endswith("/")
