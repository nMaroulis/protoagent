"""Private ProtoLink recovery storage and project-level single-writer ownership.

File preparation, mutation, revisions and restoration belong to ProtoLink.
The v0.2.1 ledger is retained for inspection; it cannot supply native revisions.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from protolink import StorageCheckpointStore
from protolink.core.resources import ResourceChange
from protolink.storage import SQLiteStorage

from . import config
from .tools import workspace_root


def private_file(path: Path) -> Path:
    """Create a private storage file before SQLite opens it; reject symlink files."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    return path


def workspace_key(workspace: str | None) -> str:
    """Use the canonical project root as the recovery namespace identity."""
    return hashlib.sha256(str(workspace_root(workspace)).encode()).hexdigest()


def checkpoint_store(workspace: str | None = None) -> StorageCheckpointStore:
    """Configure a dedicated native Storage namespace, separate from conversations."""
    if os.name != "posix":
        raise NotImplementedError("Recoverable Coder writes require POSIX with ProtoLink 0.7.1")
    database = private_file(config.CONFIG_DIR / "recovery" / f"{workspace_key(workspace)}.sqlite")
    return StorageCheckpointStore(
        SQLiteStorage(str(database), table_name="recovery", namespace="file-changes")
    )


@contextmanager
def workspace_writer(workspace: str | None):
    """Lease the native checkpoint namespace across CLI runs and undo operations."""
    if os.name != "posix":
        raise NotImplementedError("ProtoAgent's recoverable file runtime requires POSIX")
    import fcntl

    path = private_file(config.CONFIG_DIR / "recovery" / f"{workspace_key(workspace)}.lock")
    with path.open("r+b") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another ProtoAgent run owns this project's recovery store") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def legacy_checkpoints(workspace: str | None) -> list[dict[str, Any]]:
    """Read legacy inventory without modifying its database or exposing file bytes."""
    database = config.CONFIG_DIR / "checkpoints" / f"{workspace_key(workspace)}.sqlite"
    if not database.exists():
        return []
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT id, path, created_at FROM checkpoints WHERE undone = 0 "
            "ORDER BY created_at DESC, rowid DESC LIMIT 50"
        ).fetchall()
    return [{**dict(row), "state": "legacy", "restorable": False} for row in rows]


def list_checkpoints(workspace: str | None = None) -> list[dict[str, Any]]:
    """List native recovery states and retained legacy snapshots without contents."""
    records = checkpoint_store(workspace).list_changes(limit=50)
    return [
        {
            "id": change.change_id,
            "path": change.before.revision.resource_id,
            "state": change.state,
            "restorable": change.state == "applied",
            "error": change.error,
        }
        for change in records
    ] + legacy_checkpoints(workspace)


def select_change(store: StorageCheckpointStore, change_id: str, workspace: str) -> ResourceChange:
    """Resolve latest before submitting the exact native restore tool call."""
    if change_id == "latest":
        record = next(iter(store.list_changes(state="applied", limit=1)), None)
    else:
        record = store.get(change_id)
    if record is None:
        if legacy_checkpoints(workspace):
            raise ValueError(
                "No matching native checkpoint. Legacy v0.2.1 snapshots remain in the old "
                "database for manual inspection; they lack the resource revisions required for safe native undo."
            )
        raise ValueError("No matching applied checkpoint found for this project")
    if record.state != "applied":
        raise ValueError(
            f"Checkpoint is {record.state}; inspect its effect before requesting new work"
        )
    return record
