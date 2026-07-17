"""Deterministic tools used by ProtoAgent core agents."""

from __future__ import annotations

import difflib
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
    path: str, workspace: str | None = None, with_line_numbers: bool = True
) -> dict[str, Any]:
    """Read a UTF-8 text file with optional line-number formatting."""
    target = safe_path(path, workspace)
    if not target.exists():
        return {"success": False, "error": f"File not found: {path}"}
    if not target.is_file():
        return {"success": False, "error": f"Not a file: {path}"}
    if target.stat().st_size > MAX_READ_BYTES:
        return {
            "success": False,
            "error": f"File too large for a single read: {path}",
            "size_bytes": target.stat().st_size,
        }

    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {"success": False, "error": f"File is not UTF-8 text: {path}"}

    numbered = "".join(
        f"{idx + 1:4d} | {line}" for idx, line in enumerate(content.splitlines(True))
    )
    return {
        "success": True,
        "path": to_relative(target, workspace),
        "content": numbered if with_line_numbers else content,
        "raw_content": content,
        "line_count": len(content.splitlines()),
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
                                "content": line.rstrip("\n"),
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


def generate_unified_diff(
    path: str,
    updated_content: str,
    original_content: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Generate a unified-diff preview for a file replacement."""
    target = safe_path(path, workspace)
    if original_content is None:
        if target.exists():
            original = read_file(str(target), workspace, with_line_numbers=False)
            if not original.get("success"):
                return original
            original_content = original.get("raw_content", "")
        else:
            original_content = ""

    rel = to_relative(target, workspace)
    diff = "".join(
        difflib.unified_diff(
            original_content.splitlines(True),
            updated_content.splitlines(True),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
        )
    )
    return {
        "success": True,
        "path": rel,
        "diff": diff,
    }


def create_new_file(path: str, content: str, workspace: str | None = None) -> dict[str, Any]:
    """Prepare a unified-diff preview for creating a new file."""
    target = safe_path(path, workspace)
    if target.exists():
        return {"success": False, "error": f"File already exists: {path}"}
    return generate_unified_diff(path, content, original_content="", workspace=workspace)


def write_file(
    path: str,
    content: str,
    workspace: str | None = None,
    *,
    overwrite: bool = True,
) -> dict[str, Any]:
    """Write a UTF-8 file after the caller has authorized the operation."""
    target = safe_path(path, workspace)
    if target.exists() and not overwrite:
        raise FileExistsError(f"File already exists: {path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {
        "success": True,
        "path": to_relative(target, workspace),
        "bytes_written": len(content.encode("utf-8")),
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
    """Yield text-like files below a root while respecting ignore rules."""
    if not root.exists():
        return
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(
            name for name in dirs if name not in DEFAULT_IGNORES and not name.startswith(".")
        )
        for filename in sorted(files):
            if filename.startswith("."):
                continue
            path = Path(current) / filename
            if _looks_binary(path) or _safe_size(path) > MAX_READ_BYTES:
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
