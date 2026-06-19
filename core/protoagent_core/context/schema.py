"""Small structured records for Context Loom."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class IndexedFile:
    """A workspace file stored in the Context Loom index."""

    path: str
    size_bytes: int
    mtime_ns: int
    sha1: str
    language: str
    symbols: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)
    content: str = ""

    def to_row(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ContextItem:
    """A source-cited item selected for a Context Pack."""

    path: str
    language: str
    score: int
    reason: str
    evidence: list[str]
    symbols: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)
    line_range: str = ""
    snippet: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
