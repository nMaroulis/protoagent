"""Agent deck assembly for the ProtoAgent core."""

from __future__ import annotations

from typing import Any

from protolink.transport import Transport
from protolink.types import TransportType

from ..config import normalize_provider
from ..prompt_profiles import prompt_profile_status
from .architect import create_architect_agent
from .coder import create_coder_agent
from .common import AgentRuntimeAuth, create_runtime_auth
from .explorer import create_explorer_agent
from .scout import create_scout_agent


def create_agent_deck(
    registry=None,
    provider: str = "ollama",
    model: str | None = None,
    workspace: str | None = None,
    urls: dict[str, str] | None = None,
    transport: TransportType | Transport = "sse",
    approval_handler=None,
    telemetry=None,
    prompt_profile: str = "auto",
    scout_enabled: bool = False,
    auth: AgentRuntimeAuth | None = None,
) -> dict[str, Any]:
    """Create the ProtoLink agent deck using the selected LLM config.

    Every LLM-capable agent receives its own LLM instance configured with the
    same provider/model. Architect is the durable controller; Explorer and
    Coder are task-local workers. The tool-only Scout is constructed only when
    its optional-agent setting is enabled.
    """
    provider = normalize_provider(provider)
    urls = urls or {}
    auth = auth or create_runtime_auth()
    explorer = create_explorer_agent(
        registry=registry,
        provider=provider,
        model=model,
        workspace=workspace,
        url=urls.get("explorer"),
        transport=transport,
        telemetry=telemetry,
        prompt_profile=prompt_profile,
        authenticator=auth.authenticator,
        credentials=auth.credentials,
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
        authenticator=auth.authenticator,
        credentials=auth.credentials,
    )
    scout = (
        create_scout_agent(
            registry=registry,
            provider=provider,
            model=model,
            url=urls.get("scout"),
            transport=transport,
            telemetry=telemetry,
            prompt_profile=prompt_profile,
            authenticator=auth.authenticator,
            credentials=auth.credentials,
        )
        if scout_enabled
        else None
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
        scout_enabled=scout_enabled,
        authenticator=auth.authenticator,
        credentials=auth.credentials,
    )
    deck = {
        "explorer": explorer,
        "coder": coder,
    }
    if scout is not None:
        deck["scout"] = scout
    deck["architect"] = architect
    return deck


def agent_manifest(
    profile: dict[str, Any] | None = None,
    *,
    scout_enabled: bool = False,
) -> dict[str, Any]:
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
            "stateless": [
                "explorer",
                "coder",
                *(["scout"] if scout_enabled else []),
            ],
            "optional": ["scout"],
            "contract": (
                "RunContract classifies each request and marks write tasks "
                "incomplete unless Coder, a write approval/diff artifact, or "
                "an explicit blocker appears."
            ),
            "flow": [
                "Context Loom evidence",
                "RunContract",
                "ProtoLink API-key auth",
                "Architect controller",
                "Stateless specialist workers",
                *(["Optional Scout web research"] if scout_enabled else []),
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
                "enabled": True,
                "optional": False,
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
                "enabled": True,
                "optional": False,
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
                "enabled": True,
                "optional": False,
                **profile_fields,
            },
            {
                "name": "scout",
                "role": "optional stateless web research worker",
                "memory": "none",
                "persistence": "no model and no durable conversation state",
                "state": "stateless",
                "contract": (
                    "exposes ProtoLink web_search and fetch_url directly under network.read policy"
                ),
                "tools": ["web_search", "fetch_url"],
                "enabled": scout_enabled,
                "optional": True,
                "prompt_profile": "not-applicable",
                "prompt_profile_label": "Tool-only (no LLM)",
            },
        ],
    }
