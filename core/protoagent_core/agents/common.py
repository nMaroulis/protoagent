"""Shared helpers for ProtoAgent's ProtoLink agents."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from protolink.logging import QuietLogger

from ..config import normalize_provider, provider_config
from ..llm import create_llm_from_config
from ..prompt_profiles import compose_system_prompt

DEFAULT_AGENT_URLS = {
    "architect": "http://127.0.0.1:9110",
    "explorer": "http://127.0.0.1:9120",
    "coder": "http://127.0.0.1:9130",
}


QUIET_LOGGER = QuietLogger(name="protoagent-quiet")


@dataclass(frozen=True)
class AgentRuntimeAuth:
    """Shared ProtoLink authentication bundle for one embedded agent deck."""

    authenticator: object
    credentials: str


def create_runtime_auth() -> AgentRuntimeAuth:
    """Create per-run ProtoLink API-key auth for the local agent mesh."""
    from protolink.security.auth import APIKeyAuth

    token = secrets.token_urlsafe(32)
    authenticator = APIKeyAuth(
        valid_keys={token: ["agent.delegate", "workspace.read", "workspace.write"]}
    )
    return AgentRuntimeAuth(authenticator=authenticator, credentials=token)


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


def with_prompt_profile(
    system_prompt: str,
    role: str,
    provider: str,
    model: str | None,
    prompt_profile: str,
) -> str:
    """Attach the active model-capability prompt profile to a base prompt."""
    provider = normalize_provider(provider)
    cfg = provider_config(provider)
    return compose_system_prompt(
        system_prompt,
        role,
        provider=provider,
        model=model or cfg.get("model"),
        profile=prompt_profile,
        base_url=str(cfg.get("base_url") or ""),
    )
