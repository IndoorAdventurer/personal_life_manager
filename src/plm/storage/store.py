"""
JsonStore — the single persistence layer for all PLM data.

Design decisions:
- One JSON file per project  (projects/{id}.json)
- One JSON file per week     (planning/{week}.json)
- Single global inbox        (inbox.json)
- Single global profile      (profile.json)

All writes are atomic: data is written to a .tmp file on the same
filesystem, then replaced over the target via os.replace().  os.replace() is
atomic on POSIX (single rename(2) syscall) and on Windows (uses
MoveFileExW/REPLACEFILE_WRITE_THROUGH), so a crash mid-write can never leave
a partial/corrupt file behind on either platform.

The data directory defaults to ~/.local/share/plm/ but can be overridden via
the PLM_DATA_DIR environment variable (useful for testing and for pointing at
a different location on a Pi without changing code).
"""

import json
import os
import re
import warnings
from datetime import datetime, timezone
from pathlib import Path

from plm.models.inbox import InboxNote
from plm.models.planning import WeeklyPlan
from plm.models.profile import BehavioralProfile
from plm.models.project import Project

# Default XDG-style data directory.  Using XDG_DATA_HOME if set is the
# "correct" Linux convention, but we fall back to ~/.local/share/plm so the
# app works out of the box on a plain Raspberry Pi install.
_DEFAULT_DATA_DIR = Path.home() / ".local" / "share" / "plm"


class JsonStore:
    """
    Thin persistence wrapper around the PLM data directory.

    All public methods either return Pydantic model instances (so callers
    never touch raw dicts) or None / empty lists when data doesn't exist yet.
    """

    def __init__(self, data_dir: Path | None = None) -> None:
        """
        Parameters
        ----------
        data_dir:
            Override the storage root.  If None, PLM_DATA_DIR env var is
            checked first, then ~/.local/share/plm/ is used.
        """
        if data_dir is not None:
            self._root = Path(data_dir)
        elif (env := os.environ.get("PLM_DATA_DIR")):
            self._root = Path(env)
        else:
            self._root = _DEFAULT_DATA_DIR

        # Create sub-directories upfront so callers never have to worry about
        # them not existing.
        self._projects_dir.mkdir(parents=True, exist_ok=True)
        self._planning_dir.mkdir(parents=True, exist_ok=True)
        # inbox.json and profile.json live directly in _root — the root dir
        # itself is created by the mkdir above (parents=True).

    # ------------------------------------------------------------------
    # Internal path helpers
    # ------------------------------------------------------------------

    @property
    def _projects_dir(self) -> Path:
        return self._root / "projects"

    @property
    def _planning_dir(self) -> Path:
        return self._root / "planning"

    @property
    def _inbox_path(self) -> Path:
        return self._root / "inbox.json"

    @property
    def _profile_path(self) -> Path:
        return self._root / "profile.json"

    def _project_path(self, project_id: str) -> Path:
        return self._projects_dir / f"{project_id}.json"

    def _plan_path(self, week: str) -> Path:
        # Validate before using week as a filename component — an unsanitised
        # string could contain path separators (e.g. "../../../etc/passwd").
        # The ISO week format "YYYY-Www" contains only digits, a dash, and W,
        # so a simple regex is sufficient.
        if not re.fullmatch(r"\d{4}-W\d{2}", week):
            raise ValueError(
                f"Invalid ISO week string {week!r}. Expected format: 'YYYY-Www' (e.g. '2026-W10')"
            )
        return self._planning_dir / f"{week}.json"

    # ------------------------------------------------------------------
    # Atomic write helper
    # ------------------------------------------------------------------

    @staticmethod
    def _write_atomic(path: Path, text: str) -> None:
        """
        Write *text* to *path* atomically.

        Writes to a sibling .tmp file first, then calls os.replace() which
        atomically overwrites *path* on both POSIX and Windows.

        Using os.replace() rather than Path.rename() is important: on Windows,
        Path.rename() raises FileExistsError if the destination already exists,
        so the second save of any file would crash.  os.replace() avoids that.

        The .tmp file must be on the same filesystem as *path* — that's
        guaranteed here because it's a sibling — so the OS never has to do a
        cross-device copy (which would not be atomic).
        """
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------

    def list_projects(self) -> list[Project]:
        """Return all projects sorted by created_at (oldest first)."""
        projects = []
        for path in self._projects_dir.glob("*.json"):
            # Skip any leftover .tmp files from interrupted writes
            if path.suffix == ".tmp":
                continue
            try:
                projects.append(Project.model_validate_json(path.read_text("utf-8")))
            except Exception as exc:
                # A corrupt file should never silently break the whole list,
                # but it also shouldn't go completely unnoticed — the user
                # deserves to know a project file is unreadable.
                # warnings.warn() goes to stderr, which MCP clients surface in
                # their logs and which is visible in the terminal for plm-web.
                # In Chunk 6 the list_projects MCP tool can also include these
                # warnings in its structured response for in-chat visibility.
                warnings.warn(
                    f"Skipping corrupt project file {path.name!r}: {exc}",
                    stacklevel=2,
                )
        # Primary sort: target_weekly_hours descending (most committed first).
        # Projects with no target fall to the end (float("-inf") sorts below
        # any real number when reverse=True).
        # Secondary sort: updated_at descending — within the same hours tier,
        # the most recently touched project comes first.
        # NOTE: updated_at is only reliable if MCP tools remember to set it
        # before every save_project() call — the model does not auto-update it.
        projects.sort(
            key=lambda p: (
                p.target_weekly_hours if p.target_weekly_hours is not None else float("-inf"),
                p.updated_at,
            ),
            reverse=True,
        )
        return projects

    def get_project(self, project_id: str) -> Project | None:
        """Return the project with *project_id*, or None if it doesn't exist."""
        path = self._project_path(project_id)
        if not path.exists():
            return None
        return Project.model_validate_json(path.read_text("utf-8"))

    def save_project(self, project: Project) -> None:
        """
        Persist *project* to disk, creating or overwriting its file.

        Always stamps updated_at with the current UTC time before writing, so
        callers never have to remember to do it themselves.  The in-memory
        object is updated too so the caller's reference stays consistent with
        what landed on disk.
        """
        project.updated_at = datetime.now(timezone.utc)
        text = project.model_dump_json(indent=2)
        self._write_atomic(self._project_path(project.id), text)

    def delete_project(self, project_id: str) -> bool:
        """
        Delete the project file for *project_id*.

        Returns True if the file existed and was deleted, False otherwise.
        Deletion is hard — archived projects are the preferred "soft delete"
        — but this method exists so tests can clean up after themselves, and
        for any future admin tooling.
        """
        path = self._project_path(project_id)
        if path.exists():
            path.unlink()
            return True
        return False

    # ------------------------------------------------------------------
    # Weekly plans
    # ------------------------------------------------------------------

    def get_plan(self, week: str) -> WeeklyPlan | None:
        """
        Return the WeeklyPlan for *week* (e.g. "2026-W10"), or None if
        no plan has been saved for that week yet.
        """
        path = self._plan_path(week)
        if not path.exists():
            return None
        return WeeklyPlan.model_validate_json(path.read_text("utf-8"))

    def save_plan(self, plan: WeeklyPlan) -> None:
        """Persist *plan*, creating or overwriting its week file."""
        text = plan.model_dump_json(indent=2)
        self._write_atomic(self._plan_path(plan.week), text)

    # ------------------------------------------------------------------
    # Inbox
    # ------------------------------------------------------------------

    def get_inbox(self) -> list[InboxNote]:
        """
        Return all inbox notes.  If inbox.json doesn't exist yet (fresh
        install) an empty list is returned — callers don't need to special-case
        a missing file.
        """
        if not self._inbox_path.exists():
            return []
        raw = json.loads(self._inbox_path.read_text("utf-8"))
        return [InboxNote.model_validate(item) for item in raw]

    def add_inbox_note(self, note: InboxNote) -> None:
        """
        Append a single note to the inbox.

        Convenience wrapper around get_inbox() + save_inbox() so MCP tools
        don't have to repeat the read-append-write pattern themselves.
        The full list is always written atomically — see save_inbox().
        """
        notes = self.get_inbox()
        notes.append(note)
        self.save_inbox(notes)

    def save_inbox(self, notes: list[InboxNote]) -> None:
        """
        Overwrite inbox.json with the full *notes* list.

        The inbox is always saved whole (not appended) so that marking a note
        addressed, re-ordering, or bulk-clearing are all the same code path.
        Pydantic's model_dump with mode="json" handles datetime serialisation.
        """
        data = [note.model_dump(mode="json") for note in notes]
        self._write_atomic(self._inbox_path, json.dumps(data, indent=2))

    # ------------------------------------------------------------------
    # Behavioral profile
    # ------------------------------------------------------------------

    def get_profile(self) -> BehavioralProfile:
        """
        Return the stored BehavioralProfile.

        Returns a default (empty) profile if profile.json doesn't exist yet,
        so callers can always treat the return value as valid.
        """
        if not self._profile_path.exists():
            return BehavioralProfile()
        return BehavioralProfile.model_validate_json(
            self._profile_path.read_text("utf-8")
        )

    def save_profile(self, profile: BehavioralProfile) -> None:
        """Persist *profile*, creating or overwriting profile.json."""
        text = profile.model_dump_json(indent=2)
        self._write_atomic(self._profile_path, text)
