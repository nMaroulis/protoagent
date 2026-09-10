"""File checkpoints for approved Coder writes, separate from agent memory.

Snapshots preserve the exact pre-write bytes. Undo refuses to overwrite a file
that changed after the checkpoint. Commands and other tools are not covered.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import config
from .tools import generate_unified_diff, safe_path, to_relative, workspace_root

MISSING = "missing"


def fingerprint(content: bytes | None) -> str:
    """Distinguish a missing file from every possible content hash."""
    return MISSING if content is None else hashlib.sha256(content).hexdigest()


def file_content(path: Path) -> bytes | None:
    """Read existing bytes, rejecting directories and unsupported file types."""
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError(f"Not a regular file: {path}")
    return path.read_bytes()


@contextmanager
def _database(workspace: str | None):
    """Open the private checkpoint ledger for one canonical project root."""
    key = hashlib.sha256(str(workspace_root(workspace)).encode()).hexdigest()
    directory = config.CONFIG_DIR / "checkpoints"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    database = directory / f"{key}.sqlite"
    connection = sqlite3.connect(database, timeout=10)
    try:
        os.chmod(database, 0o600)
        connection.row_factory = sqlite3.Row
        connection.execute(
            "CREATE TABLE IF NOT EXISTS checkpoints ("
            "id TEXT PRIMARY KEY, path TEXT NOT NULL, before BLOB, after_hash TEXT NOT NULL, "
            "mode INTEGER NOT NULL, created_at REAL NOT NULL, undone INTEGER NOT NULL DEFAULT 0)"
        )
        connection.commit()
        yield connection
    finally:
        connection.close()


def list_checkpoints(workspace: str | None = None) -> list[dict[str, Any]]:
    """List the latest 50 recoverable file snapshots without exposing contents."""
    with _database(workspace) as db:
        rows = db.execute(
            "SELECT id, path, created_at FROM checkpoints WHERE undone = 0 "
            "ORDER BY created_at DESC, rowid DESC LIMIT 50"
        ).fetchall()
    return [dict(row) for row in rows]


def _checkpoint(checkpoint_id: str, workspace: str | None) -> dict[str, Any]:
    """Resolve an explicit checkpoint or the latest active one for this project."""
    with _database(workspace) as db:
        if checkpoint_id == "latest":
            row = db.execute(
                "SELECT * FROM checkpoints WHERE undone = 0 ORDER BY created_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
        else:
            row = db.execute(
                "SELECT * FROM checkpoints WHERE id = ? AND undone = 0", (checkpoint_id,)
            ).fetchone()
    if row is None:
        raise ValueError("No active checkpoint found for this project")
    return dict(row)


def _replace(path: Path, content: bytes, mode: int) -> None:
    """Replace a file atomically, preserving its preexisting permission bits."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_change(
    path: str, content: str, expected_hash: str, workspace: str | None = None
) -> dict[str, Any]:
    """Checkpoint and apply a ProtoLink-authorized write if its preview is current.

    Persist the snapshot before touching the file, so an interrupted write still
    has recovery evidence. A snapshot whose write did not land will fail the
    same content comparison as any conflicting user change.
    """
    target = safe_path(path, workspace)
    before = file_content(target)
    if fingerprint(before) != expected_hash:
        raise ValueError("File changed since approval preview; request a fresh diff")
    after = content.encode("utf-8")
    if before == after:
        return {"success": True, "path": to_relative(target, workspace), "changed": False}
    mode = stat.S_IMODE(target.stat().st_mode) if before is not None else 0o644
    checkpoint_id = uuid.uuid4().hex
    with _database(workspace) as db:
        db.execute(
            "INSERT INTO checkpoints (id, path, before, after_hash, mode, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                checkpoint_id,
                to_relative(target, workspace),
                before,
                fingerprint(after),
                mode,
                time.time(),
            ),
        )
        db.commit()
    # Recheck after the ledger write; a stale preview must never silently win.
    if fingerprint(file_content(safe_path(path, workspace))) != expected_hash:
        raise ValueError("File changed while preparing the checkpoint; request a fresh diff")
    _replace(target, after, mode)
    return {
        "success": True,
        "path": to_relative(target, workspace),
        "changed": True,
        "bytes_written": len(after),
        "checkpoint_id": checkpoint_id,
    }


def restore_preview(checkpoint_id: str, workspace: str | None = None) -> dict[str, Any]:
    """Build an undo diff only if the current file still matches the agent write."""
    row = _checkpoint(checkpoint_id, workspace)
    target = safe_path(row["path"], workspace)
    current = file_content(target)
    if fingerprint(current) != row["after_hash"]:
        raise ValueError("Undo conflict: file changed after the agent write; nothing was restored")
    before = row["before"]
    preview = generate_unified_diff(
        row["path"],
        (before or b"").decode("utf-8"),
        original_content=(current or b"").decode("utf-8"),
        workspace=workspace,
    )
    if before is None:
        preview["diff"] = preview["diff"].replace(f"+++ b/{row['path']}\n", "+++ /dev/null\n", 1)
    return {**preview, "checkpoint_id": row["id"], "expected_hash": row["after_hash"]}


def restore_checkpoint(
    checkpoint_id: str, expected_hash: str, workspace: str | None = None
) -> dict[str, Any]:
    """Restore one approved checkpoint without clobbering subsequent user edits."""
    row = _checkpoint(checkpoint_id, workspace)
    target = safe_path(row["path"], workspace)
    if expected_hash != row["after_hash"] or fingerprint(file_content(target)) != expected_hash:
        raise ValueError("Undo conflict: file changed since the preview; nothing was restored")
    if row["before"] is None:
        target.unlink()
    else:
        _replace(target, row["before"], row["mode"])
    with _database(workspace) as db:
        db.execute("UPDATE checkpoints SET undone = 1 WHERE id = ?", (row["id"],))
        db.commit()
    return {"success": True, "path": row["path"], "checkpoint_id": row["id"], "restored": True}
