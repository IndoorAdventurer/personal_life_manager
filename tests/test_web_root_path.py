"""
Tests for rendering behind a reverse-proxy subpath (PLM_ROOT_PATH=/plm).

In production Caddy serves the app under /plm, so request.url.path carries
the prefix ("/plm/inbox").  Template logic that matches on the path — the
active nav tab and the swipe navigation's tab index — must strip it, or the
swipe script bails out as if the page were not a tab.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import plm.web.app as app_module
from plm.storage.store import JsonStore

_TEST_PASSWORD = "test-password"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(app_module, "store", JsonStore(data_dir=tmp_path))
    monkeypatch.setattr(app_module, "_PLM_PASSWORD", _TEST_PASSWORD)
    with TestClient(app_module.app, root_path="/plm") as c:
        c.post("/plm/login", data={"password": _TEST_PASSWORD}, follow_redirects=True)
        yield c


@pytest.mark.parametrize("path, index, label", [
    ("/plm/",         0, "Projects"),
    ("/plm/planning", 1, "Planning"),
    ("/plm/inbox",    2, "Inbox"),
    ("/plm/profile",  3, "Profile"),
])
def test_tab_detected_under_root_path(client: TestClient, path: str, index: int, label: str) -> None:
    html = client.get(path).text
    assert f"var current = {index};" in html
    assert re.search(rf'class="active">{label}</a>', html)
