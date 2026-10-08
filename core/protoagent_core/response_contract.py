"""Validate user-facing completion through ProtoLink's native lifecycle hook."""

from __future__ import annotations

import json


def unfinished_action(content: object) -> bool:
    """Recognize a bare action-shaped answer without translating or executing it."""
    if not isinstance(content, str):
        return False
    try:
        value = json.loads(content)
    except (ValueError, TypeError):
        return False
    if not isinstance(value, dict):
        return False
    action = value.get("type") in ("tool_call", "agent_call")
    for key in ("tool_call", "agent_call", "function_call"):
        nested = value.get(key)
        if isinstance(nested, dict) and any(field in nested for field in ("name", "tool", "agent")):
            action = True
    return action


def invalid_prior_answer(content: str) -> bool:
    """Identify old bad finals while preserving real historical action messages."""
    try:
        value = json.loads(content)
    except ValueError:
        return False
    if not isinstance(value, dict):
        return False
    if value.get("type") == "final":
        return unfinished_action(value.get("content"))
    if value.get("type") in ("tool_call", "agent_call"):
        return False  # These are valid action-history entries, not final text.
    return unfinished_action(content)


def validate_final_answer(response) -> None:
    """Reject a bare unfinished action; never turn answer text into execution.

    Ordinary JSON answers, prose and documented code examples remain answers.
    Bare runtime requests belong to the engine's action channel. A rejection
    aborts the native run before its final event and conversation commit.
    """
    if unfinished_action(response.content):
        raise ValueError(
            "Model returned an unfinished tool or worker request instead of an answer. "
            "No action was dispatched from this text. Use /trace to inspect the run; "
            "retry with native tool calling or a model that follows the action protocol. "
            "When explaining the protocol, put examples in a code block or accompanying prose."
        )
