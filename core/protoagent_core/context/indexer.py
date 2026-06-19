"""Workspace indexing for Context Loom."""

from __future__ import annotations

import ast
import hashlib
import os
import re
import time
from pathlib import Path
from typing import Any

from ..tools import DEFAULT_IGNORES, MAX_READ_BYTES, to_relative, workspace_root
from .schema import IndexedFile
from .store import ContextStore

MAX_INDEX_FILES = 1_200
MAX_INDEX_CONTENT_CHARS = 80_000


def refresh_context_index(workspace: str | None = None, max_files: int = MAX_INDEX_FILES) -> dict[str, Any]:
    """Refresh the deterministic local index for a workspace."""
    started = time.time()
    root = workspace_root(workspace)
    store = ContextStore(str(root))
    indexed: list[IndexedFile] = []
    live_paths: set[str] = set()
    skipped = 0

    for path in _walk_indexable_files(root):
        if len(indexed) >= max_files:
            skipped += 1
            continue
        rel = to_relative(path, str(root))
        live_paths.add(rel)
        item = _index_file(path, rel)
        if item is None:
            skipped += 1
            continue
        indexed.append(item)

    updated = store.upsert_files(indexed)
    removed = store.delete_missing(live_paths)
    duration_ms = int((time.time() - started) * 1000)
    store.mark_indexed(duration_ms, len(live_paths), updated, removed)
    status = store.status()
    status.update(
        {
            "success": True,
            "files_seen": len(live_paths),
            "files_updated": updated,
            "files_removed": removed,
            "files_skipped": skipped,
            "duration_ms": duration_ms,
        }
    )
    return status


def _walk_indexable_files(root: Path):
    if not root.exists():
        return
    for current, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if _include_dir(name)]
        for filename in sorted(files):
            if filename.startswith("."):
                continue
            path = Path(current) / filename
            if _looks_binary(path) or _safe_size(path) > MAX_READ_BYTES:
                continue
            yield path


def _include_dir(name: str) -> bool:
    return name not in DEFAULT_IGNORES and not name.startswith(".")


def _index_file(path: Path, rel: str) -> IndexedFile | None:
    try:
        stat = path.stat()
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    language = _detect_language(path)
    symbols, imports, headings = _extract_metadata(content, language)
    compact_content = _compact_content(content)
    return IndexedFile(
        path=rel,
        size_bytes=int(stat.st_size),
        mtime_ns=int(stat.st_mtime_ns),
        sha1=hashlib.sha1(content.encode("utf-8", errors="ignore")).hexdigest(),
        language=language,
        symbols=symbols[:80],
        imports=imports[:80],
        headings=headings[:80],
        content=compact_content,
    )


def _detect_language(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".py": "python",
        ".rs": "rust",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".md": "markdown",
        ".toml": "toml",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".html": "html",
        ".css": "css",
    }.get(suffix, suffix.lstrip(".") or "text")


def _extract_metadata(content: str, language: str) -> tuple[list[str], list[str], list[str]]:
    if language == "python":
        return _python_metadata(content)
    if language == "rust":
        return _regex_metadata(
            content,
            symbol_patterns=[
                r"\b(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)",
                r"\b(?:pub\s+)?struct\s+([A-Za-z_][A-Za-z0-9_]*)",
                r"\b(?:pub\s+)?enum\s+([A-Za-z_][A-Za-z0-9_]*)",
                r"\b(?:pub\s+)?trait\s+([A-Za-z_][A-Za-z0-9_]*)",
                r"\bimpl\s+([A-Za-z_][A-Za-z0-9_]*)",
            ],
            import_pattern=r"^\s*use\s+([^;]+);",
        )
    if language in {"javascript", "typescript"}:
        return _regex_metadata(
            content,
            symbol_patterns=[
                r"\bfunction\s+([A-Za-z_$][A-Za-z0-9_$]*)",
                r"\bclass\s+([A-Za-z_$][A-Za-z0-9_$]*)",
                r"\b(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=",
                r"\bexport\s+(?:async\s+)?function\s+([A-Za-z_$][A-Za-z0-9_$]*)",
            ],
            import_pattern=r"^\s*import\s+(.+?)(?:from\s+['\"][^'\"]+['\"])?;?",
        )
    headings = _markdown_headings(content) if language == "markdown" else []
    return [], [], headings


def _python_metadata(content: str) -> tuple[list[str], list[str], list[str]]:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return _regex_metadata(
            content,
            symbol_patterns=[r"^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)", r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)"],
            import_pattern=r"^\s*(?:from\s+([A-Za-z0-9_.]+)\s+import|import\s+([A-Za-z0-9_., ]+))",
        )
    symbols: list[str] = []
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.append(node.name)
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append("." * node.level + (node.module or ""))
    return _unique(symbols), _unique(imports), []


def _regex_metadata(
    content: str,
    *,
    symbol_patterns: list[str],
    import_pattern: str,
) -> tuple[list[str], list[str], list[str]]:
    symbols: list[str] = []
    for pattern in symbol_patterns:
        symbols.extend(match.group(1).strip() for match in re.finditer(pattern, content, flags=re.MULTILINE))
    imports: list[str] = []
    for match in re.finditer(import_pattern, content, flags=re.MULTILINE):
        imports.extend(group.strip() for group in match.groups() if group and group.strip())
    return _unique(symbols), _unique(imports), []


def _markdown_headings(content: str) -> list[str]:
    return _unique(
        match.group(1).strip()
        for match in re.finditer(r"^\s{0,3}#{1,6}\s+(.+)$", content, flags=re.MULTILINE)
    )


def _compact_content(content: str) -> str:
    lines = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lines.append(stripped)
        if sum(len(item) for item in lines) > MAX_INDEX_CONTENT_CHARS:
            break
    return "\n".join(lines)[:MAX_INDEX_CONTENT_CHARS]


def _looks_binary(path: Path) -> bool:
    return path.suffix.lower() in {
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
        ".sqlite",
    }


def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _unique(values) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        value = str(value).strip()
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out
