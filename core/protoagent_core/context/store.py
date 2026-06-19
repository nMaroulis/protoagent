"""SQLite storage for Context Loom indexes."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

from ..tools import workspace_root
from .schema import IndexedFile


def context_config_dir() -> Path:
    """Return the ProtoAgent config directory used for Context Loom indexes."""
    raw_dir = os.getenv("PROTOAGENT_CONFIG_DIR")
    config_dir = Path(raw_dir).expanduser() if raw_dir else Path.home() / ".protoagent"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def workspace_key(workspace: str | None = None) -> str:
    """Return a stable key for a workspace path."""
    root = str(workspace_root(workspace))
    return hashlib.sha1(root.encode("utf-8")).hexdigest()[:16]


class ContextStore:
    """Thin SQLite wrapper for one workspace index."""

    def __init__(self, workspace: str | None = None):
        self.workspace = str(workspace_root(workspace))
        index_dir = context_config_dir() / "indexes"
        index_dir.mkdir(parents=True, exist_ok=True)
        self.path = index_dir / f"{workspace_key(self.workspace)}.sqlite"

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        self._ensure_schema(conn)
        return conn

    def status(self) -> dict[str, Any]:
        with self.connect() as conn:
            count = conn.execute("select count(*) from files").fetchone()[0]
            meta = {
                row["key"]: row["value"]
                for row in conn.execute("select key, value from metadata")
            }
        return {
            "name": "Context Loom",
            "workspace": self.workspace,
            "index_path": str(self.path),
            "files_indexed": count,
            "indexed_at": meta.get("indexed_at", ""),
            "last_duration_ms": int(meta.get("last_duration_ms", "0") or 0),
            "schema": int(meta.get("schema", "1") or 1),
        }

    def read_file(self, path: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("select * from files where path = ?", (path,)).fetchone()
        return _row_to_entry(row) if row else None

    def read_all(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("select * from files order by path").fetchall()
        return [_row_to_entry(row) for row in rows]

    def upsert_files(self, files: Iterable[IndexedFile]) -> int:
        count = 0
        with self.connect() as conn:
            for item in files:
                conn.execute(
                    """
                    insert into files (
                        path, size_bytes, mtime_ns, sha1, language,
                        symbols_json, imports_json, headings_json, content
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    on conflict(path) do update set
                        size_bytes = excluded.size_bytes,
                        mtime_ns = excluded.mtime_ns,
                        sha1 = excluded.sha1,
                        language = excluded.language,
                        symbols_json = excluded.symbols_json,
                        imports_json = excluded.imports_json,
                        headings_json = excluded.headings_json,
                        content = excluded.content
                    """,
                    (
                        item.path,
                        item.size_bytes,
                        item.mtime_ns,
                        item.sha1,
                        item.language,
                        json.dumps(item.symbols, ensure_ascii=True),
                        json.dumps(item.imports, ensure_ascii=True),
                        json.dumps(item.headings, ensure_ascii=True),
                        item.content,
                    ),
                )
                count += 1
        return count

    def delete_missing(self, live_paths: set[str]) -> int:
        with self.connect() as conn:
            rows = [row["path"] for row in conn.execute("select path from files")]
            stale = [path for path in rows if path not in live_paths]
            conn.executemany("delete from files where path = ?", ((path,) for path in stale))
        return len(stale)

    def update_metadata(self, values: dict[str, Any]) -> None:
        with self.connect() as conn:
            for key, value in values.items():
                conn.execute(
                    "insert into metadata(key, value) values(?, ?) "
                    "on conflict(key) do update set value = excluded.value",
                    (key, str(value)),
                )

    def mark_indexed(self, duration_ms: int, files_seen: int, files_updated: int, files_removed: int) -> None:
        self.update_metadata(
            {
                "schema": 1,
                "indexed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "last_duration_ms": duration_ms,
                "files_seen": files_seen,
                "files_updated": files_updated,
                "files_removed": files_removed,
            }
        )

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            create table if not exists metadata (
                key text primary key,
                value text not null
            )
            """
        )
        conn.execute(
            """
            create table if not exists files (
                path text primary key,
                size_bytes integer not null,
                mtime_ns integer not null,
                sha1 text not null,
                language text not null,
                symbols_json text not null,
                imports_json text not null,
                headings_json text not null,
                content text not null
            )
            """
        )


def _row_to_entry(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "path": row["path"],
        "size_bytes": int(row["size_bytes"]),
        "mtime_ns": int(row["mtime_ns"]),
        "sha1": row["sha1"],
        "language": row["language"],
        "symbols": _loads(row["symbols_json"]),
        "imports": _loads(row["imports_json"]),
        "headings": _loads(row["headings_json"]),
        "content": row["content"],
    }


def _loads(value: str) -> list[str]:
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return []
    return [str(item) for item in loaded if isinstance(item, str)]
