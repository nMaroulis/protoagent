"""Deterministic tools used by ProtoAgent core agents."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path
from typing import Any

DEFAULT_IGNORES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "__pycache__",
    "node_modules",
    "target",
    "dist",
    "build",
}
MAX_READ_BYTES = 240_000
MAX_SEARCH_RESULTS = 120


def workspace_root(workspace: str | None = None) -> Path:
    """Resolve the active workspace root."""
    raw = workspace or os.getenv("PROTOAGENT_WORKSPACE") or os.getcwd()
    return Path(raw).expanduser().resolve()


def safe_path(path: str, workspace: str | None = None) -> Path:
    """Resolve a path and reject access outside the workspace."""
    root = workspace_root(workspace)
    target = Path(path).expanduser()
    if not target.is_absolute():
        target = root / target
    resolved = target.resolve()
    if root != resolved and root not in resolved.parents:
        raise ValueError(f"Access denied outside workspace: {path}")
    return resolved


def to_relative(path: Path, workspace: str | None = None) -> str:
    """Return a workspace-relative path when possible."""
    try:
        return str(path.resolve().relative_to(workspace_root(workspace)))
    except ValueError:
        return str(path)


def read_file(
    path: str,
    workspace: str | None = None,
    with_line_numbers: bool = True,
    start_line: int = 1,
    end_line: int | None = None,
    max_chars: int = 8192,
) -> dict[str, Any]:
    """Read one bounded source span and return its full-file SHA-256 revision."""
    target = safe_path(path, workspace)
    if not target.is_file():
        return {"success": False, "error": f"Not a file: {path}"}
    if start_line < 1 or (end_line is not None and end_line < start_line):
        return {"success": False, "error": "Invalid line range"}
    if target.stat().st_size > 8 * 1024 * 1024:
        return {"success": False, "error": "File exceeds the 8 MiB read ceiling"}
    try:
        data = target.read_bytes()
        lines = data.decode("utf-8").splitlines(keepends=True)
    except (OSError, UnicodeDecodeError) as exc:
        return {"success": False, "error": str(exc)}
    limit = max(256, min(max_chars, MAX_READ_BYTES))
    stop = min(len(lines), end_line if end_line is not None else start_line + 119)
    rows = []
    used = 0
    last = start_line - 1
    partial = False
    for index in range(start_line - 1, stop):
        row = f"{index + 1:4d} | {lines[index]}" if with_line_numbers else lines[index]
        if used + len(row) > limit:
            if not rows:
                rows.append(row[:limit])
                partial = True
                last = index + 1
            break
        rows.append(row)
        used += len(row)
        last = index + 1
    return {
        "success": True,
        "path": to_relative(target, workspace),
        "content": "".join(rows),
        "revision": hashlib.sha256(data).hexdigest(),
        "line_count": len(lines),
        "start_line": start_line,
        "end_line": last,
        "truncated": partial or last < len(lines),
        "partial_line": partial,
        "next_line": last if partial else last + 1,
    }


def list_directory(path: str = ".", workspace: str | None = None) -> dict[str, Any]:
    """List non-ignored entries in a workspace directory."""
    target = safe_path(path, workspace)
    if not target.exists():
        return {"success": False, "error": f"Directory not found: {path}"}
    if not target.is_dir():
        return {"success": False, "error": f"Not a directory: {path}"}

    entries = []
    try:
        children = sorted(
            target.iterdir(), key=lambda child: (not child.is_dir(), child.name.lower())
        )
    except OSError as exc:
        return {"success": False, "error": str(exc)}

    for child in children:
        if child.name in DEFAULT_IGNORES:
            continue
        entry = {
            "name": child.name,
            "path": to_relative(child, workspace),
            "type": "directory" if child.is_dir() else "file",
        }
        if child.is_file():
            try:
                entry["size_bytes"] = child.stat().st_size
            except OSError:
                entry["size_bytes"] = None
        entries.append(entry)

    return {
        "success": True,
        "path": to_relative(target, workspace),
        "entries": entries,
        "count": len(entries),
    }


def search_regex(
    pattern: str,
    path: str = ".",
    file_filter: str = ".*",
    workspace: str | None = None,
) -> dict[str, Any]:
    """Search workspace text files with a regular expression."""
    root = safe_path(path, workspace)
    if not root.exists():
        return {"success": False, "error": f"Path not found: {path}"}
    try:
        regex = re.compile(pattern)
        file_regex = re.compile(file_filter)
    except re.error as exc:
        return {"success": False, "error": f"Invalid regex: {exc}"}

    files = [root] if root.is_file() else _walk_text_files(root)
    matches = []
    files_searched = 0
    for file_path in files:
        rel = to_relative(file_path, workspace)
        if not file_regex.search(rel):
            continue
        files_searched += 1
        try:
            with file_path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, 1):
                    if regex.search(line):
                        matches.append(
                            {
                                "file": rel,
                                "line": line_number,
                                "content": line.rstrip("\n")[:500],
                            }
                        )
                    if len(matches) >= MAX_SEARCH_RESULTS:
                        break
        except (OSError, UnicodeDecodeError):
            continue
        if len(matches) >= MAX_SEARCH_RESULTS:
            break

    return {
        "success": True,
        "pattern": pattern,
        "path": to_relative(root, workspace),
        "matches": matches,
        "total_matches": len(matches),
        "files_searched": files_searched,
        "truncated": len(matches) >= MAX_SEARCH_RESULTS,
    }


def get_git_status(workspace: str | None = None) -> dict[str, Any]:
    """Return `git status --short` for the workspace."""
    root = workspace_root(workspace)
    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"success": False, "error": str(exc)}

    return {
        "success": result.returncode == 0,
        "status": result.stdout.splitlines(),
        "error": result.stderr.strip(),
    }


def build_context_map(workspace: str | None = None, max_files: int = 80) -> dict[str, Any]:
    """Build a compact workspace file and git-status summary."""
    root = workspace_root(workspace)
    files = []
    for file_path in _walk_text_files(root):
        files.append(
            {
                "path": to_relative(file_path, workspace),
                "size_bytes": _safe_size(file_path),
            }
        )
        if len(files) >= max_files:
            break
    return {
        "success": True,
        "workspace": str(root),
        "files": files,
        "git": get_git_status(str(root)),
    }


def _walk_text_files(root: Path):
    """Yield bounded text files, skipping ignored names and symlink entries."""
    if not root.exists():
        return
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(
            name
            for name in dirs
            if name not in DEFAULT_IGNORES
            and not name.startswith(".")
            and not (Path(current) / name).is_symlink()
        )
        for filename in sorted(files):
            if filename.startswith("."):
                continue
            path = Path(current) / filename
            if (
                path.is_symlink()
                or not path.is_file()
                or _looks_binary(path)
                or _safe_size(path) > MAX_READ_BYTES
            ):
                continue
            yield path


def _looks_binary(path: Path) -> bool:
    """Return true for file types the text tools should skip."""
    binary_suffixes = {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".pdf",
        ".zip",
        ".tar",
        ".gz",
        ".bin",
        ".so",
        ".dylib",
        ".class",
        ".pyc",
    }
    return path.suffix.lower() in binary_suffixes


def _safe_size(path: Path) -> int:
    """Return file size, or zero when stat fails."""
    try:
        return path.stat().st_size
    except OSError:
        return 0
