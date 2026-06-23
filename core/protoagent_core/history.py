"""ProtoLink-owned conversation history policy for ProtoAgent."""

from __future__ import annotations

import os
from typing import Any, Iterable

from protolink.state.conversation import ConversationState

from .agents.common import conversation_storage
from .llm import create_llm_from_config

AGENT_NAMES = ("architect", "explorer", "coder")
DEFAULT_HISTORY_BUDGET_RATIO = 0.7


def compact_agent_histories_for_run(
    agents: Iterable[Any],
    session_id: str | None,
) -> list[dict[str, Any]]:
    """Apply ProtoLink token compaction before agents resume a session.

    The model profile supplies the real context window. Histories without a
    configured window remain untouched; the LLM's built-in compaction tool is
    still available for explicit user requests during inference.
    """
    if not session_id:
        return []

    reports: list[dict[str, Any]] = []
    ratio = _history_budget_ratio()
    for agent in agents:
        llm = getattr(agent, "llm", None)
        profile = getattr(llm, "metrics_profile", None)
        context_window = getattr(profile, "context_window", None)
        if llm is None or not context_window:
            continue

        max_tokens = max(1_024, int(context_window * ratio))
        state = ConversationState(agent.storage)
        if session_id not in state.to_dict():
            continue

        llm.history = state.get_history(session_id, default_system_prompt=llm.system_prompt)
        result = llm.compact_history(
            strategy="tokens",
            max_tokens=max_tokens,
            preserve_recent=6,
        )
        report = {
            "agent": str(agent.card.name),
            "context_window": context_window,
            "max_tokens": max_tokens,
            **result.to_dict(),
        }
        reports.append(report)
        if result.changed:
            state.save_history(session_id, llm.history)
    return reports


def compact_saved_histories(
    session_id: str,
    provider: str,
    model: str | None,
    *,
    strategy: str = "tokens",
    limit: int | None = None,
) -> dict[str, Any]:
    """Compact every agent's durable session through ``LLM.compact_history``."""
    if strategy not in {"recent", "tokens", "summary"}:
        raise ValueError("Compaction strategy must be recent, tokens, or summary")

    reports: list[dict[str, Any]] = []
    for agent_name in AGENT_NAMES:
        storage = conversation_storage(agent_name)
        if storage is None:
            reports.append({"agent": agent_name, "found": False, "error": "storage unavailable"})
            continue
        state = ConversationState(storage)
        if session_id not in state.to_dict():
            reports.append({"agent": agent_name, "found": False})
            continue

        llm = create_llm_from_config(provider, model)
        llm.history = state.get_history(session_id, default_system_prompt=llm.system_prompt)
        options = _compaction_options(llm, strategy, limit)
        try:
            result = llm.compact_history(strategy=strategy, **options)
        except Exception as exc:
            reports.append({"agent": agent_name, "found": True, "error": str(exc)})
            continue
        if result.changed:
            state.save_history(session_id, llm.history)
        reports.append(
            {
                "agent": agent_name,
                "found": True,
                **result.to_dict(),
            }
        )

    return {
        "session_id": session_id,
        "strategy": strategy,
        "agents": reports,
        "found": any(report.get("found") for report in reports),
        "removed_messages": sum(int(report.get("removed_messages", 0)) for report in reports),
        "errors": [
            f"{report['agent']}: {report['error']}"
            for report in reports
            if report.get("error")
        ],
    }


def reset_saved_histories(session_id: str) -> dict[str, Any]:
    """Clear one durable ProtoLink session across the full agent deck."""
    cleared: list[str] = []
    for agent_name in AGENT_NAMES:
        storage = conversation_storage(agent_name)
        if storage is None:
            continue
        state = ConversationState(storage)
        if session_id not in state.to_dict():
            continue
        state.clear_session(session_id)
        cleared.append(agent_name)
    return {"session_id": session_id, "cleared_agents": cleared, "found": bool(cleared)}


def _compaction_options(llm: Any, strategy: str, limit: int | None) -> dict[str, int]:
    if strategy == "recent":
        return {"max_messages": limit or 20}
    if strategy == "summary":
        return {"preserve_recent": limit or 6}

    if limit is not None:
        return {"max_tokens": limit, "preserve_recent": 6}
    profile = getattr(llm, "metrics_profile", None)
    context_window = getattr(profile, "context_window", None)
    max_tokens = int(context_window * _history_budget_ratio()) if context_window else 4_000
    return {"max_tokens": max(1_024, max_tokens), "preserve_recent": 6}


def _history_budget_ratio() -> float:
    raw = os.getenv("PROTOAGENT_HISTORY_BUDGET_RATIO", str(DEFAULT_HISTORY_BUDGET_RATIO))
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_HISTORY_BUDGET_RATIO
    return min(0.9, max(0.2, value))
