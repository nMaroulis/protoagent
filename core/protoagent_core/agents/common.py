"""Shared helpers for ProtoAgent's ProtoLink agents."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..config import normalize_provider
from ..llm import create_llm_from_config

DEFAULT_AGENT_URLS = {
    "architect": "http://127.0.0.1:9110",
    "explorer": "http://127.0.0.1:9120",
    "coder": "http://127.0.0.1:9130",
}


class QuietLogger:
    """Small logger shim for embedded CLI runs."""

    def debug(self, *_args, **_kwargs):
        pass

    def info(self, *_args, **_kwargs):
        pass

    def warning(self, *_args, **_kwargs):
        pass

    def error(self, *_args, **_kwargs):
        pass

    def exception(self, *_args, **_kwargs):
        pass


QUIET_LOGGER = QuietLogger()


class SessionAwareAgentMixin:
    """Propagate ProtoAgent conversation sessions through agent delegation."""

    async def call_llm(self, infer_part, task=None, **kwargs):
        previous = getattr(self, "_protoagent_session_id", None)
        if task is not None:
            session_id = task.metadata.get("session_id")
            if session_id:
                self._protoagent_session_id = session_id
        try:
            return await super().call_llm(infer_part, task=task, **kwargs)
        finally:
            if previous is None:
                if hasattr(self, "_protoagent_session_id"):
                    delattr(self, "_protoagent_session_id")
            else:
                self._protoagent_session_id = previous

    async def _handle_agent_call(self, agent_name: str, action: str, payload: dict[str, Any]) -> Any:
        from protolink.core.message import Message
        from protolink.core.part import Part
        from protolink.core.task import Task

        agent_url = await self._resolve_agent_url(agent_name)
        if agent_url == self.card.url:
            raise ValueError(
                f"Self-delegation is not allowed. You are '{self.card.name}' ({self.card.url}) and cannot delegate tasks to yourself."
            )

        if action == "tool_call":
            tool_name = payload.get("tool")
            args = payload.get("args", {})
            if not tool_name:
                raise ValueError(f"tool_call agent_call must specify 'tool' field. Received payload: {payload}")
            task = Task.create(Message(role="agent", parts=[Part.tool_call(tool_name=tool_name, args=args)]))
        elif action == "infer":
            task = Task.create(Message.infer(prompt=payload.get("prompt", "")))
        else:
            raise ValueError(f"Unknown agent_call action: {action}")

        session_id = getattr(self, "_protoagent_session_id", None)
        if session_id:
            task.metadata["session_id"] = session_id
            task.metadata["parent_agent"] = self.card.name
            task.metadata["delegated_agent"] = agent_name

        result_task = await self.call_agent(agent_url, task)
        return result_task.get_last_part_content()


def session_aware_agent_class():
    """Return an Agent class that preserves session metadata during delegation."""
    from protolink.agents import Agent

    class SessionAwareAgent(SessionAwareAgentMixin, Agent):
        pass

    return SessionAwareAgent


def create_selected_llm(provider: str, model: str | None = None):
    """Create a ProtoLink LLM object from the user's active provider/model."""
    return create_llm_from_config(normalize_provider(provider), model)


def conversation_storage(agent_name: str):
    """Create durable ProtoLink storage for conversation state."""
    try:
        from protolink.storage import SQLiteStorage
    except Exception:
        return None

    raw_dir = os.getenv("PROTOAGENT_CONFIG_DIR")
    config_dir = Path(raw_dir).expanduser() if raw_dir else Path.home() / ".protoagent"
    config_dir.mkdir(parents=True, exist_ok=True)
    return SQLiteStorage(
        db_path=str(config_dir / "conversations.sqlite"),
        table_name="agent_state",
        namespace=f"protoagent-{agent_name}",
    )


def resolve_agent_url(name: str, explicit_url: str | None = None) -> str:
    """Resolve an agent URL from explicit runtime config, env, or defaults."""
    if explicit_url:
        return explicit_url
    env_name = f"{name.upper()}_AGENT_URL"
    return os.getenv(env_name, DEFAULT_AGENT_URLS[name])


def set_transport_timeout(transport, timeout: int) -> None:
    """Apply a long request timeout across ProtoLink transport implementations."""
    if hasattr(transport, "timeout"):
        transport.timeout = timeout
    elif hasattr(transport, "_timeout"):
        transport._timeout = timeout


def with_workspace_contract(system_prompt: str, workspace: str | None, role: str) -> str:
    """Attach the active project contract to an agent system prompt."""
    project = workspace or os.getenv("PROTOAGENT_WORKSPACE") or os.getcwd()
    return (
        f"{system_prompt.rstrip()}\n\n"
        "Active project contract:\n"
        f"- Current project root: {project}\n"
        "- Treat every relative path as relative to that project root.\n"
        "- Never invent or use a random folder outside the project root.\n"
        "- If a user asks to create or modify a file, produce an approval-gated file action rather than only explaining code.\n"
        "- If no path is specified, choose a conservative project-relative path only when the request is obvious; otherwise ask one concise clarification.\n"
        f"- You are the {role}; identify your role when handing work to another agent or producing the final response.\n"
    )


def record_side_effect(side_effects: list[dict[str, Any]] | None, payload: dict[str, Any]) -> None:
    """Record tool-produced payloads for the Rust CLI surface."""
    if side_effects is not None:
        side_effects.append(payload)
