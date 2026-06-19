"""Shared helpers for ProtoAgent's ProtoLink agents."""

from __future__ import annotations

import os
from pathlib import Path

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
