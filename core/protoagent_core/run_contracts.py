"""Run contracts and completion checks for ProtoAgent tasks.

The model can choose a poor route even when prompts are clear. This module
keeps the route expectation outside the prompt by deriving a small contract from
the original user request and checking the finished ProtoLink trace against it.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

_WRITE_HINTS = (
    "add",
    "change",
    "create",
    "delete",
    "document",
    "edit",
    "fix",
    "implement",
    "improve",
    "move",
    "patch",
    "polish",
    "refactor",
    "remove",
    "rename",
    "replace",
    "update",
    "write",
)
_WRITE_NOUN_HINTS = (
    "docstring",
    "docs",
    "documentation",
    "linting",
    "migration",
    "type checking",
)
_READ_ONLY_PREFIX = re.compile(
    r"^\s*(?:please\s+)?(explain|what|why|where|who|when|how\s+(does|do|is|are|can)|"
    r"identify|find|show|summarize|review|read|research|search|look\s+up|lookup|run|execute|verify|check|build|test|lint)\b",
    re.IGNORECASE,
)
_READ_ONLY_WRITE_CLAUSE = re.compile(
    r"(?:\b(?:and|then|also|but)\b|[,:;.!?])\s+(?:please\s+)?(?:"
    + "|".join(re.escape(hint) for hint in _WRITE_HINTS)
    + r")\b|\bplease\s+(?:"
    + "|".join(re.escape(hint) for hint in _WRITE_HINTS)
    + r")\b",
    re.IGNORECASE,
)
_VERIFICATION_PREFIX = re.compile(
    r"^\s*(?:please\s+)?(?:run|execute|verify|check|build|test|lint)\b", re.IGNORECASE
)
_GREETING = re.compile(r"^\s*(hi|hello|hey|thanks|thank you)\W*$", re.IGNORECASE)


@dataclass(frozen=True)
class RunContract:
    """A compact, runtime-visible completion contract for one user request."""

    task_kind: str
    requires_explorer: bool
    requires_coder: bool
    requires_write: bool
    expected_workers: tuple[str, ...]
    expected_artifacts: tuple[str, ...]
    completion_rule: str
    reason: str
    forbids_write: bool = False
    allows_unverified_docs: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable contract for RunContext metadata."""
        data = asdict(self)
        data["expected_workers"] = list(self.expected_workers)
        data["expected_artifacts"] = list(self.expected_artifacts)
        return data


def infer_run_contract(user_prompt: str) -> RunContract:
    """Infer the worker and artifact requirements for ``user_prompt``.

    The inference is deliberately conservative. It only makes write completion a
    hard runtime condition when the user appears to be asking ProtoAgent to alter
    the workspace, not when they ask for an explanation or proposal.
    """
    prompt = re.sub(
        r"^\s*(?:(?:can|could|would|will) you\s+|please\s+)+",
        "",
        user_prompt.strip(),
        flags=re.IGNORECASE,
    )
    if not prompt or _GREETING.match(prompt):
        return RunContract(
            task_kind="direct-answer",
            requires_explorer=False,
            requires_coder=False,
            requires_write=False,
            expected_workers=(),
            expected_artifacts=(),
            completion_rule="Direct answers may complete without worker delegation.",
            reason="Prompt is empty or conversational.",
        )

    forbidden_write = bool(
        re.search(
            r"\b(?:do not|don't|never) (?:change|edit|modify|write)\b|\bread[- ]only\b",
            prompt,
            re.IGNORECASE,
        )
    )
    write_intent = not forbidden_write and _has_write_intent(prompt)
    verification_intent = not write_intent and bool(_VERIFICATION_PREFIX.match(prompt))
    expected_workers = ("explorer", "coder") if write_intent else ("explorer",)
    if verification_intent:
        expected_workers = ("explorer", "verifier")
    expected_artifacts = ("executed_file_change", "resource_revision") if write_intent else ()
    return RunContract(
        task_kind=(
            "workspace-change"
            if write_intent
            else "workspace-verification"
            if verification_intent
            else "repository-question"
        ),
        requires_explorer=True,
        requires_coder=write_intent,
        requires_write=write_intent,
        expected_workers=expected_workers,
        expected_artifacts=expected_artifacts,
        completion_rule=(
            "Workspace changes require an executed native file change at its current "
            "resource revision; approval alone cannot satisfy completion."
            if write_intent
            else "Repository questions should use evidence, but no write artifact is required."
        ),
        reason=(
            "Prompt contains an implementation or modification verb."
            if write_intent
            else "Prompt reads as analysis or explanation rather than modification."
        ),
        forbids_write=forbidden_write,
        allows_unverified_docs=write_intent and _documentation_only(prompt),
    )


def _has_write_intent(prompt: str) -> bool:
    text = prompt.lower()
    if _READ_ONLY_PREFIX.match(text):
        # Read-only questions often mention implementation verbs inside symbol
        # names or prose ("how does create_agent_deck work?"). Only turn such a
        # question into a write contract when it contains a separate imperative
        # clause such as "and update the docs" or "Review it. Fix the bug."
        return bool(_READ_ONLY_WRITE_CLAUSE.search(text))
    tokens = set(re.findall(r"[a-z][a-z0-9_-]*", text))
    if tokens.intersection(_WRITE_HINTS):
        return True
    return any(hint in text for hint in _WRITE_NOUN_HINTS)


def _documentation_only(prompt: str) -> bool:
    """Conservative exception: code requests cannot complete by writing only a note."""
    mentions_docs = re.search(
        r"\b(?:docs?|documentation|readme|whitepaper|changelog)\b|\.(?:md|rst|txt)\b",
        prompt,
        re.IGNORECASE,
    )
    code_target = re.search(
        r"\.(?:py|rs|js|jsx|ts|tsx|go|c|h|cpp|java|rb|sh)\b|\bin (?:the )?code\b|\b(?:codebase|code changes|implementation changes)\b",
        prompt,
        re.IGNORECASE,
    )
    code_change = re.search(
        r"\b(?:fix|implement|change|refactor|patch|update|add|remove)\s+(?:the\s+)?(?:code|function|runtime|module|api|logic|behavior|feature|tests?)\b",
        prompt,
        re.IGNORECASE,
    )
    requested_check = re.search(
        r"\b(?:run|execute)\s+(?:the\s+)?(?:tests?|checks?|lint|build|pytest|unittest)\b|\band\s+(?:verify|test|lint|build)\b",
        prompt,
        re.IGNORECASE,
    )
    return bool(mentions_docs and not code_target and not code_change and not requested_check)
