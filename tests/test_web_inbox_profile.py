"""
Tests for Inbox and Profile web routes (sub-chunk 8d-1).

Covers:
  Inbox  — page render, add note, empty content rejected, mark addressed,
           mark unaddressed, delete, delete unknown note
  Profile — page render (empty + populated), update content, update with
            change summary appended to history, markdown rendered as HTML

TODO: these tests were not deeply reviewed by me (Vincent) — worth a closer
read at some point.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

import plm.web.app as app_module
from plm.models.inbox import InboxNote
from plm.models.profile import BehavioralProfile, ProfileUpdate
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

def _add_note(store: JsonStore, content: str = "Test note", addressed: bool = False) -> InboxNote:
    note = InboxNote(content=content, addressed=addressed)
    store.add_inbox_note(note)
    return note


def _set_profile(store: JsonStore, content: str, summary: str = "") -> BehavioralProfile:
    from datetime import datetime, timezone
    profile = BehavioralProfile(
        content=content,
        last_updated=datetime.now(timezone.utc),
        history=[ProfileUpdate(summary=summary)] if summary else [],
    )
    store.save_profile(profile)
    return profile


# ===========================================================================
# Inbox — auth guard
# ===========================================================================

def test_inbox_requires_auth(store: JsonStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_module, "store", store)
    monkeypatch.setattr(app_module, "_PLM_PASSWORD", _TEST_PASSWORD)
    with TestClient(app_module.app, raise_server_exceptions=True) as c:
        resp = c.get("/inbox", follow_redirects=True)
        assert resp.url.path == "/login"


# ===========================================================================
# Inbox — page render
# ===========================================================================

def test_inbox_empty_state(client: TestClient) -> None:
    resp = client.get("/inbox")
    assert resp.status_code == 200
    assert "inbox is clear" in resp.text.lower()


def test_inbox_shows_unaddressed_notes(client: TestClient, store: JsonStore) -> None:
    _add_note(store, "Buy groceries")
    _add_note(store, "Call dentist")
    resp = client.get("/inbox")
    assert resp.status_code == 200
    assert "Buy groceries" in resp.text
    assert "Call dentist" in resp.text


def test_inbox_addressed_notes_in_addressed_section(client: TestClient, store: JsonStore) -> None:
    _add_note(store, "Pending task")
    _add_note(store, "Done task", addressed=True)
    resp = client.get("/inbox")
    assert "Pending task" in resp.text
    assert "Done task" in resp.text
    # The "Addressed" heading should only appear when there are addressed notes
    assert "Addressed" in resp.text


# ===========================================================================
# Inbox — add note
# ===========================================================================

def test_inbox_add_note(client: TestClient, store: JsonStore) -> None:
    resp = client.post(
        "/inbox/notes",
        data={"content": "My new idea"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "My new idea" in resp.text
    notes = store.get_inbox()
    assert len(notes) == 1
    assert notes[0].content == "My new idea"


def test_inbox_add_note_empty_content_rejected(client: TestClient, store: JsonStore) -> None:
    resp = client.post(
        "/inbox/notes",
        data={"content": "   "},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    # Flash error should appear
    assert "cannot be empty" in resp.text.lower()
    assert len(store.get_inbox()) == 0


# ===========================================================================
# Inbox — mark addressed / unaddressed
# ===========================================================================

def test_inbox_mark_addressed(client: TestClient, store: JsonStore) -> None:
    note = _add_note(store, "Do laundry")
    resp = client.post(
        f"/inbox/notes/{note.id}/address",
        follow_redirects=True,
    )
    assert resp.status_code == 200
    updated = store.get_inbox()[0]
    assert updated.addressed is True
    assert updated.addressed_at is not None


def test_inbox_mark_unaddressed(client: TestClient, store: JsonStore) -> None:
    note = _add_note(store, "Old task", addressed=True)
    resp = client.post(
        f"/inbox/notes/{note.id}/address",
        follow_redirects=True,
    )
    assert resp.status_code == 200
    updated = store.get_inbox()[0]
    assert updated.addressed is False
    # addressed_at cleared when un-addressing
    assert updated.addressed_at is None


def test_inbox_address_unknown_note_flashes_error(client: TestClient) -> None:
    resp = client.post(
        "/inbox/notes/nonexistent-id/address",
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "not found" in resp.text.lower()


# ===========================================================================
# Inbox — delete
# ===========================================================================

def test_inbox_delete_note(client: TestClient, store: JsonStore) -> None:
    note = _add_note(store, "Temporary note")
    resp = client.post(
        f"/inbox/notes/{note.id}/delete",
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert len(store.get_inbox()) == 0


def test_inbox_delete_leaves_other_notes(client: TestClient, store: JsonStore) -> None:
    n1 = _add_note(store, "Keep this")
    n2 = _add_note(store, "Delete this")
    client.post(f"/inbox/notes/{n2.id}/delete", follow_redirects=True)
    remaining = store.get_inbox()
    assert len(remaining) == 1
    assert remaining[0].id == n1.id


def test_inbox_delete_unknown_note_flashes_error(client: TestClient) -> None:
    resp = client.post(
        "/inbox/notes/nonexistent-id/delete",
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "not found" in resp.text.lower()


# ===========================================================================
# Profile — auth guard
# ===========================================================================

def test_profile_requires_auth(store: JsonStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_module, "store", store)
    monkeypatch.setattr(app_module, "_PLM_PASSWORD", _TEST_PASSWORD)
    with TestClient(app_module.app, raise_server_exceptions=True) as c:
        resp = c.get("/profile", follow_redirects=True)
        assert resp.url.path == "/login"


# ===========================================================================
# Profile — page render
# ===========================================================================

def test_profile_empty_state(client: TestClient) -> None:
    resp = client.get("/profile")
    assert resp.status_code == 200
    # The "no profile yet" message should appear
    assert "no profile" in resp.text.lower()


def test_profile_shows_content(client: TestClient, store: JsonStore) -> None:
    _set_profile(store, "I work best in the **morning**.")
    resp = client.get("/profile")
    assert resp.status_code == 200
    # markdown converts **morning** → <strong>morning</strong>
    assert "<strong>morning</strong>" in resp.text


def test_profile_shows_history(client: TestClient, store: JsonStore) -> None:
    _set_profile(store, "Some content", summary="Initial setup")
    resp = client.get("/profile")
    assert resp.status_code == 200
    assert "Initial setup" in resp.text


# ===========================================================================
# Profile — update
# ===========================================================================

def test_profile_update_content(client: TestClient, store: JsonStore) -> None:
    resp = client.post(
        "/profile",
        data={"content": "Updated content", "change_summary": ""},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    profile = store.get_profile()
    assert profile.content == "Updated content"
    assert profile.last_updated is not None
    # No history entry when summary is empty
    assert len(profile.history) == 0


def test_profile_update_with_summary_appends_history(client: TestClient, store: JsonStore) -> None:
    client.post(
        "/profile",
        data={"content": "I prefer morning work", "change_summary": "Added morning preference"},
        follow_redirects=True,
    )
    profile = store.get_profile()
    assert len(profile.history) == 1
    assert profile.history[0].summary == "Added morning preference"


def test_profile_update_preserves_existing_history(client: TestClient, store: JsonStore) -> None:
    _set_profile(store, "Old content", summary="First entry")
    client.post(
        "/profile",
        data={"content": "New content", "change_summary": "Second entry"},
        follow_redirects=True,
    )
    profile = store.get_profile()
    assert len(profile.history) == 2
    summaries = [h.summary for h in profile.history]
    assert "First entry" in summaries
    assert "Second entry" in summaries


def test_profile_update_flash_success(client: TestClient) -> None:
    resp = client.post(
        "/profile",
        data={"content": "Something", "change_summary": ""},
        follow_redirects=True,
    )
    assert "Profile saved" in resp.text


def test_profile_update_empty_content_clears_profile(client: TestClient, store: JsonStore) -> None:
    _set_profile(store, "Some old content")
    client.post(
        "/profile",
        data={"content": "   ", "change_summary": ""},
        follow_redirects=True,
    )
    profile = store.get_profile()
    # Stripping whitespace-only content results in empty string
    assert profile.content == ""
