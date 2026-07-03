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
    "test",
    "tests",
    "type checking",
)
_READ_ONLY_PREFIX = re.compile(
    r"^\s*(explain|what|why|where|who|when|how\s+(does|do|is|are|can)|"
    r"identify|find|show|summarize|review)\b",
    re.IGNORECASE,
)
_GREETING = re.compile(r"^\s*(hi|hello|hey|thanks|thank you)\W*$", re.IGNORECASE)
_BLOCKER_HINTS = (
    "blocked",
    "can't proceed",
    "cannot proceed",
    "could not proceed",
    "need clarification",
    "need more context",
    "no path",
    "not enough context",
    "unable to proceed",
)


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

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable contract for RunContext metadata."""
        data = asdict(self)
        data["expected_workers"] = list(self.expected_workers)
        data["expected_artifacts"] = list(self.expected_artifacts)
        return data


@dataclass(frozen=True)
class CompletionValidation:
    """Result of comparing a completed ProtoLink run with its contract."""

    outcome: str
    satisfied: bool
    used_explorer: bool
    used_coder: bool
    approval_requested: bool
    diff_present: bool
    explicit_blocker: bool
    message: str
    missing: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable validation report."""
        data = asdict(self)
        data["missing"] = list(self.missing)
        return data


def infer_run_contract(user_prompt: str) -> RunContract:
    """Infer the worker and artifact requirements for ``user_prompt``.

    The inference is deliberately conservative. It only makes write completion a
    hard runtime condition when the user appears to be asking ProtoAgent to alter
    the workspace, not when they ask for an explanation or proposal.
    """
    prompt = user_prompt.strip()
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

    write_intent = _has_write_intent(prompt)
    expected_workers = ("explorer", "coder") if write_intent else ("explorer",)
    expected_artifacts = ("approval_request", "diff_preview") if write_intent else ()
    return RunContract(
        task_kind="workspace-change" if write_intent else "repository-question",
        requires_explorer=True,
        requires_coder=write_intent,
        requires_write=write_intent,
        expected_workers=expected_workers,
        expected_artifacts=expected_artifacts,
        completion_rule=(
            "Workspace changes must reach Coder, a write approval/diff preview, "
            "or an explicit blocker before the run is terminal."
            if write_intent
            else "Repository questions should use evidence, but no write artifact is required."
        ),
        reason=(
            "Prompt contains an implementation or modification verb."
            if write_intent
            else "Prompt reads as analysis or explanation rather than modification."
        ),
    )


def validate_run_completion(
    contract: RunContract,
    *,
    answer: str,
    status: str,
    run_events: list[Any],
    approval_requests: list[Any],
    diff_items: list[Any],
) -> CompletionValidation:
    """Validate a finished run against a ``RunContract``."""
    if status == "canceled":
        return CompletionValidation(
            outcome="canceled",
            satisfied=True,
            used_explorer=False,
            used_coder=False,
            approval_requested=False,
            diff_present=False,
            explicit_blocker=False,
            message="Run was canceled before completion validation.",
        )

    used_explorer = _used_agent(run_events, "explorer")
    used_coder = _used_agent(run_events, "coder")
    approval_requested = bool(approval_requests)
    diff_present = _has_diff(diff_items)
    explicit_blocker = _has_blocker(answer)

    missing: list[str] = []
    if contract.requires_coder and not (used_coder or approval_requested or diff_present):
        missing.append(
            "Coder worker was required but no Coder delegation or write artifact appeared."
        )
    if contract.requires_write and not (approval_requested or diff_present):
        missing.append(
            "Workspace write was required but no approval request or diff preview appeared."
        )

    if missing and explicit_blocker:
        return CompletionValidation(
            outcome="blocked",
            satisfied=True,
            used_explorer=used_explorer,
            used_coder=used_coder,
            approval_requested=approval_requested,
            diff_present=diff_present,
            explicit_blocker=True,
            message="Run ended with an explicit blocker instead of a write artifact.",
            missing=tuple(missing),
        )

    if missing:
        return CompletionValidation(
            outcome="incomplete",
            satisfied=False,
            used_explorer=used_explorer,
            used_coder=used_coder,
            approval_requested=approval_requested,
            diff_present=diff_present,
            explicit_blocker=explicit_blocker,
            message="Run ended before satisfying the required worker/artifact contract.",
            missing=tuple(missing),
        )

    outcome = "satisfied" if contract.requires_write else "not-required"
    message = (
        "Run satisfied the required write completion contract."
        if contract.requires_write
        else "Run did not require a write completion contract."
    )
    return CompletionValidation(
        outcome=outcome,
        satisfied=True,
        used_explorer=used_explorer,
        used_coder=used_coder,
        approval_requested=approval_requested,
        diff_present=diff_present,
        explicit_blocker=explicit_blocker,
        message=message,
    )


def _has_write_intent(prompt: str) -> bool:
    text = prompt.lower()
    if _READ_ONLY_PREFIX.match(text) and not any(hint in text for hint in _WRITE_HINTS):
        return False
    tokens = set(re.findall(r"[a-z][a-z0-9_-]*", text))
    if tokens.intersection(_WRITE_HINTS):
        return True
    return any(hint in text for hint in _WRITE_NOUN_HINTS)


def _used_agent(run_events: list[Any], agent_name: str) -> bool:
    agent_name = agent_name.lower()
    for event in run_events:
        if not isinstance(event, dict):
            continue
        raw_payload = event.get("payload")
        payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
        raw_metadata = payload.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        llm_type = str(payload.get("llm_event_type") or "").lower()
        if (
            llm_type == "agent_call_start"
            and str(metadata.get("agent") or "").lower() == agent_name
        ):
            return True
        if str(event.get("agent_name") or "").lower() == agent_name and llm_type in {
            "tool_start",
            "llm_step",
            "llm_final",
        }:
            return True
    return False


def _has_diff(diff_items: list[Any]) -> bool:
    for item in diff_items:
        if isinstance(item, dict) and str(item.get("diff") or "").strip():
            return True
        if isinstance(item, str) and item.strip():
            return True
    return False


def _has_blocker(answer: str) -> bool:
    text = answer.lower()
    return any(hint in text for hint in _BLOCKER_HINTS)
