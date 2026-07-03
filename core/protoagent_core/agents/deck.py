"""Agent deck assembly for the ProtoAgent core."""

from __future__ import annotations

from typing import Any

from protolink.types import TransportType

from ..config import normalize_provider
from ..prompt_profiles import prompt_profile_status
from .architect import create_architect_agent
from .coder import create_coder_agent
from .explorer import create_explorer_agent


def create_agent_deck(
    registry=None,
    provider: str = "ollama",
    model: str | None = None,
    workspace: str | None = None,
    urls: dict[str, str] | None = None,
    transport: TransportType = "sse",
    approval_handler=None,
    telemetry=None,
    prompt_profile: str = "auto",
) -> dict[str, Any]:
    """Create the ProtoLink agent deck using the selected LLM config.

    Every LLM-capable agent receives its own LLM instance configured with the
    same provider/model. Architect is the durable controller; Explorer and
    Coder are task-local workers with isolated prompts and no persisted
    conversation state.
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
        telemetry=telemetry,
        prompt_profile=prompt_profile,
    )
    coder = create_coder_agent(
        registry=registry,
        provider=provider,
        model=model,
        workspace=workspace,
        url=urls.get("coder"),
        transport=transport,
        approval_handler=approval_handler,
        telemetry=telemetry,
        prompt_profile=prompt_profile,
    )
    architect = create_architect_agent(
        registry=registry,
        provider=provider,
        model=model,
        workspace=workspace,
        url=urls.get("architect"),
        transport=transport,
        telemetry=telemetry,
        prompt_profile=prompt_profile,
    )
    return {
        "explorer": explorer,
        "coder": coder,
        "architect": architect,
    }


def agent_manifest(profile: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the visible runtime architecture and worker manifest."""
    profile = profile or prompt_profile_status({"active_provider": "ollama", "providers": {}})
    profile_fields = {
        "prompt_profile": str(profile.get("resolved", "")),
        "prompt_profile_label": str(profile.get("label", "")),
    }
    return {
        "architecture": {
            "kernel": "ProtoLink runtime kernel",
            "controller": "architect",
            "stateful": [
                "architect conversation memory",
                "Context Loom workspace index",
                "RunContext, RunRecorder, policy, approvals, reports",
            ],
            "stateless": ["explorer", "coder"],
            "contract": (
                "RunContract classifies each request and marks write tasks "
                "incomplete unless Coder, a write approval/diff artifact, or "
                "an explicit blocker appears."
            ),
            "flow": [
                "Context Loom evidence",
                "RunContract",
                "Architect controller",
                "Stateless specialist workers",
                "ProtoLink policy gate",
                "RunReport",
            ],
        },
        "agents": [
            {
                "name": "architect",
                "role": "stateful controller",
                "memory": "protoagent-architect",
                "persistence": "durable conversation memory",
                "state": "stateful",
                "contract": "routes by RunContract; no direct workspace tools",
                "tools": [],
                **profile_fields,
            },
            {
                "name": "explorer",
                "role": "stateless context worker",
                "memory": "task-local",
                "persistence": "no durable conversation state",
                "state": "stateless",
                "contract": "returns read-only evidence for the current task",
                "tools": [
                    "build_context_pack",
                    "read_file",
                    "list_directory",
                    "search_regex",
                    "get_git_status",
                ],
                **profile_fields,
            },
            {
                "name": "coder",
                "role": "stateless write worker",
                "memory": "task-local",
                "persistence": "no durable conversation state",
                "state": "stateless",
                "contract": "prepares RunAction diff artifacts behind approval",
                "tools": ["generate_unified_diff", "create_new_file"],
                **profile_fields,
            },
        ],
    }
