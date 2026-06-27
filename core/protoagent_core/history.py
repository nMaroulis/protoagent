"""ProtoLink-owned conversation state controls for ProtoAgent."""

from __future__ import annotations

import asyncio
import os
from typing import Any, Iterable

from protolink import Agent, CapabilityPolicy, StateOperationResult
from protolink.llms.metrics import estimate_token_count
from protolink.state.conversation import ConversationState

from .agents.common import QUIET_LOGGER, conversation_storage, with_workspace_contract

AGENT_NAMES = ("architect", "explorer", "coder")
DEFAULT_HISTORY_BUDGET_RATIO = 0.7


async def compact_agent_histories_for_run(
    agents: Iterable[Any],
    session_id: str | None,
) -> list[dict[str, Any]]:
    """Compact each running agent's persisted conversation through ProtoLink."""
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
        result = await agent.compact_state(
            session_id=session_id,
            strategy="tokens",
            max_tokens=max_tokens,
            preserve_recent=6,
        )
        reports.append(
            _compact_agent_report(
                _agent_name(agent),
                result,
                context_window=context_window,
                max_tokens=max_tokens,
            )
        )
    return reports


def compact_saved_histories(
    session_id: str,
    provider: str,
    model: str | None,
    *,
    strategy: str = "tokens",
    limit: int | None = None,
) -> dict[str, Any]:
    """Compact every agent's durable session through ProtoLink state APIs."""
    return asyncio.run(
        _compact_saved_histories(
            session_id,
            provider,
            model,
            strategy=strategy,
            limit=limit,
        )
    )


def reset_saved_histories(session_id: str) -> dict[str, Any]:
    """Clear one durable ProtoLink session across the full agent deck."""
    return asyncio.run(_reset_saved_histories(session_id))


def describe_saved_histories(session_id: str, *, recent_messages: int = 6) -> dict[str, Any]:
    """Return a read-only summary of model-facing ProtoLink conversation state."""
    return asyncio.run(_describe_saved_histories(session_id, recent_messages=recent_messages))


def persist_architect_turn(
    session_id: str | None,
    *,
    workspace: str | None,
    user_prompt: str,
    assistant_answer: str,
) -> dict[str, Any]:
    """Ensure the top-level Architect turn exists in ProtoLink conversation state.

    ProtoLink normally saves this through ``Agent.handle_task_streaming()``.
    This app-level guard fills gaps in the top-level session while leaving an
    already persisted current turn untouched.
    """
    if not session_id or not user_prompt.strip() or not assistant_answer.strip():
        return {"agent": "architect", "changed": False, "reason": "empty session or turn"}
    storage = conversation_storage("architect")
    if storage is None:
        return {"agent": "architect", "changed": False, "reason": "storage unavailable"}

    state = ConversationState(storage)
    history = state.get_history(session_id, default_system_prompt=_architect_system_prompt(workspace))
    existing = history.to_list()
    if _has_current_top_level_turn(existing, user_prompt, assistant_answer):
        return {
            "agent": "architect",
            "changed": False,
            "reason": "already persisted",
            "message_count": len(existing),
        }

    previous_count = len(existing)
    history.add_user(user_prompt)
    history.add_assistant(assistant_answer)
    state.save_history(session_id, history)
    return {
        "agent": "architect",
        "changed": True,
        "previous_message_count": previous_count,
        "message_count": len(history),
    }


async def _compact_saved_histories(
    session_id: str,
    provider: str,
    model: str | None,
    *,
    strategy: str,
    limit: int | None,
) -> dict[str, Any]:
    if strategy not in {"recent", "tokens", "summary"}:
        raise ValueError("Compaction strategy must be recent, tokens, or summary")

    reports: list[dict[str, Any]] = []
    for agent_name, agent in _compaction_agents(provider, model):
        options = _compaction_options(agent, strategy, limit)
        result = await agent.compact_state(
            session_id=session_id,
            strategy=strategy,
            **options,
        )
        reports.append(_compact_agent_report(agent_name, result))

    return _compact_summary(session_id, strategy, reports)


async def _reset_saved_histories(session_id: str) -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    cleared: list[str] = []
    for agent_name, agent in _control_agents():
        result = await agent.reset_state(session_id=session_id, stores=("conversation",))
        report = _state_agent_report(agent_name, result)
        reports.append(report)
        if _state_existed_before(result):
            cleared.append(agent_name)
    return {
        "session_id": session_id,
        "agents": reports,
        "cleared_agents": cleared,
        "found": bool(cleared),
        "state_results": [report["state_result"] for report in reports],
    }


async def _describe_saved_histories(session_id: str, *, recent_messages: int) -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    for agent_name, agent in _control_agents():
        result = await agent.describe_state(
            session_id=session_id,
            stores=("conversation",),
            include_data=True,
        )
        reports.append(_describe_agent_report(agent_name, result, recent_messages=recent_messages))
    return {
        "session_id": session_id,
        "agents": reports,
        "found": any(report.get("found") for report in reports),
        "state_results": [report["state_result"] for report in reports],
    }


def _control_agents() -> list[tuple[str, Agent]]:
    return [(name, _control_agent(name)) for name in AGENT_NAMES]


def _control_agent(agent_name: str) -> Agent:
    return Agent(
        card={
            "name": agent_name,
            "description": f"ProtoAgent {agent_name} state control facade.",
            "url": f"runtime://protoagent-state-{agent_name}",
        },
        transport=None,
        llm=None,
        storage=conversation_storage(agent_name),
        state=["conversation"],
        policy=_state_policy(),
        logger=QUIET_LOGGER,
        verbosity=0,
    )


def _compaction_agents(provider: str, model: str | None) -> list[tuple[str, Any]]:
    from .agents.architect import create_architect_agent
    from .agents.coder import create_coder_agent
    from .agents.explorer import create_explorer_agent

    return [
        ("architect", create_architect_agent(provider=provider, model=model, transport=None)),
        ("explorer", create_explorer_agent(provider=provider, model=model, transport=None)),
        ("coder", create_coder_agent(provider=provider, model=model, transport=None)),
    ]


def _state_policy() -> CapabilityPolicy:
    return CapabilityPolicy(
        {
            "llm.history.compact": "allow",
            "state.compact": "allow",
            "state.describe": "allow",
            "state.reset": "allow",
        },
        default_effect="deny",
    )


def _compaction_options(agent: Any, strategy: str, limit: int | None) -> dict[str, int]:
    if strategy == "recent":
        return {"max_messages": limit or 20}
    if strategy == "summary":
        return {"preserve_recent": limit or 6}

    if limit is not None:
        return {"max_tokens": limit, "preserve_recent": 6}
    llm = getattr(agent, "llm", None)
    profile = getattr(llm, "metrics_profile", None)
    context_window = getattr(profile, "context_window", None)
    max_tokens = int(context_window * _history_budget_ratio()) if context_window else 4_000
    return {"max_tokens": max(1_024, max_tokens), "preserve_recent": 6}


def _compact_summary(session_id: str, strategy: str, reports: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "strategy": strategy,
        "agents": reports,
        "found": any(report.get("found") for report in reports),
        "removed_messages": sum(int(report.get("removed_messages", 0)) for report in reports),
        "errors": [
            f"{report['agent']}: {error['message']}"
            for report in reports
            for error in report.get("errors", [])
        ],
        "state_results": [report["state_result"] for report in reports],
    }


def _compact_agent_report(
    agent_name: str,
    result: StateOperationResult,
    *,
    context_window: int | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    store = _conversation_store(result)
    compaction = _compaction_metadata(store)
    found = bool(store and store.exists)
    report = {
        "agent": agent_name,
        "found": found,
        "changed": bool(compaction.get("changed", False)),
        "removed_messages": int(compaction.get("removed_messages", 0) or 0),
        "before_tokens": int(compaction.get("before_tokens", 0) or 0),
        "after_tokens": int(compaction.get("after_tokens", 0) or 0),
        "context_window": context_window,
        "max_tokens": max_tokens,
        "errors": [dict(error) for error in result.errors],
        "state_result": result.to_dict(),
    }
    if store is not None:
        report["message_count"] = store.message_count
    return report


def _describe_agent_report(
    agent_name: str,
    result: StateOperationResult,
    *,
    recent_messages: int,
) -> dict[str, Any]:
    store = _conversation_store(result)
    messages = store.data if store is not None and isinstance(store.data, list) else []
    found = bool(store and store.exists and messages)
    return {
        "agent": agent_name,
        "found": found,
        "message_count": len(messages) if messages else (store.message_count if store else 0),
        "estimated_tokens": estimate_token_count(messages) if messages else 0,
        "recent": [
            {
                "role": str(message.get("role", "unknown")),
                "name": str(message.get("name", "") or ""),
                "preview": _preview_text(message.get("content")),
            }
            for message in messages[-recent_messages:]
        ],
        "errors": [dict(error) for error in result.errors],
        "state_result": result.to_dict(),
    }


def _state_agent_report(agent_name: str, result: StateOperationResult) -> dict[str, Any]:
    store = _conversation_store(result)
    return {
        "agent": agent_name,
        "found": _state_existed_before(result),
        "cleared": bool(store and store.cleared),
        "message_count": store.message_count if store else 0,
        "errors": [dict(error) for error in result.errors],
        "state_result": result.to_dict(),
    }


def _conversation_store(result: StateOperationResult):
    return next((store for store in result.stores if store.name == "conversation"), None)


def _compaction_metadata(store) -> dict[str, Any]:
    if store is None:
        return {}
    compaction = store.metadata.get("compaction") if isinstance(store.metadata, dict) else None
    return dict(compaction or {})


def _state_existed_before(result: StateOperationResult) -> bool:
    store = _conversation_store(result)
    if store is None or not isinstance(store.metadata, dict):
        return False
    before = store.metadata.get("before")
    if isinstance(before, dict):
        return bool(before.get("exists"))
    return bool(store.exists)


def _agent_name(agent: Any) -> str:
    card = getattr(agent, "card", None)
    return str(getattr(card, "name", None) or "agent")


def _has_current_top_level_turn(
    messages: list[dict[str, Any]],
    user_prompt: str,
    assistant_answer: str,
) -> bool:
    """Return true when ProtoLink already saved the current top-level turn."""
    if len(messages) < 2:
        return False
    user = messages[-2]
    assistant = messages[-1]
    if user.get("role") != "user" or assistant.get("role") != "assistant":
        return False
    if _normalize_message_text(assistant.get("content")) != _normalize_message_text(
        assistant_answer
    ):
        return False
    return _matches_user_prompt(user.get("content"), user_prompt)


def _matches_user_prompt(content: Any, user_prompt: str) -> bool:
    actual = _normalize_message_text(content)
    expected = _normalize_message_text(user_prompt)
    if actual == expected:
        return True
    return actual.endswith(f"Current user request: {expected}")


def _normalize_message_text(value: Any) -> str:
    return " ".join(_content_text(value).split())


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item))
        return " ".join(part for part in parts if part)
    return str(value or "")


def _architect_system_prompt(workspace: str | None) -> str:
    from .agents.architect import ARCHITECT_SYSTEM_PROMPT

    return with_workspace_contract(ARCHITECT_SYSTEM_PROMPT, workspace, "Architect")


def _history_budget_ratio() -> float:
    raw = os.getenv("PROTOAGENT_HISTORY_BUDGET_RATIO", str(DEFAULT_HISTORY_BUDGET_RATIO))
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_HISTORY_BUDGET_RATIO
    return min(0.9, max(0.2, value))


def _preview_text(value: Any, *, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."
