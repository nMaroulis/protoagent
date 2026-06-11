"""Agent deck assembly for the ProtoAgent core."""

from __future__ import annotations

from typing import Any

from ..config import normalize_provider
from .architect import create_architect_agent
from .coder import create_coder_agent
from .explorer import create_explorer_agent


def create_agent_deck(
    registry=None,
    provider: str = "ollama",
    model: str | None = None,
    workspace: str | None = None,
    urls: dict[str, str] | None = None,
    transport: str = "sse",
    side_effects: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create the ProtoLink agent deck using the selected LLM config.

    Every LLM-capable agent receives its own LLM instance configured with the
    same provider/model. Separate instances keep per-agent prompts and histories
    isolated while still honoring the user's model selection.
    """
    provider = normalize_provider(provider)
    urls = urls or {}
    explorer = create_explorer_agent(
        registry=registry,
        provider=provider,
        model=model,
        workspace=workspace,
        url=urls.get("explorer"),
        transport=transport,
    )
    coder = create_coder_agent(
        registry=registry,
        provider=provider,
        model=model,
        workspace=workspace,
        url=urls.get("coder"),
        transport=transport,
        side_effects=side_effects,
    )
    architect = create_architect_agent(
        registry=registry,
        provider=provider,
        model=model,
        workspace=workspace,
        url=urls.get("architect"),
        transport=transport,
        side_effects=side_effects,
    )
    return {
        "explorer": explorer,
        "coder": coder,
        "architect": architect,
    }


def agent_manifest() -> dict[str, Any]:
    """Static manifest used by the CLI doctor and fallback mode."""
    return {
        "agents": [
            {
                "name": "architect",
                "role": "orchestrator",
                "tools": ["request_user_approval"],
            },
            {
                "name": "explorer",
                "role": "context",
                "tools": ["read_file", "list_directory", "search_regex", "get_git_status"],
            },
            {
                "name": "coder",
                "role": "synthesis",
                "tools": ["generate_unified_diff", "create_new_file"],
            },
        ]
    }
