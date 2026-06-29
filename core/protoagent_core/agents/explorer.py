"""Explorer agent factory."""

from __future__ import annotations

from typing import Any

from protolink import Agent, CapabilityPolicy
from protolink.types import TransportType

from .. import tools
from ..context import build_context_pack as loom_context_pack
from .common import (
    QUIET_LOGGER,
    conversation_storage,
    create_selected_llm,
    resolve_agent_url,
    set_transport_timeout,
    with_prompt_profile,
    with_workspace_contract,
)

EXPLORER_SYSTEM_PROMPT = """You are the ProtoAgent Explorer.

Build dense context maps for coding tasks. You may read files, list directories,
search with regexes, ask Context Loom for a source-cited pack, and inspect git
status. You must never modify files or execute arbitrary shell commands.

Return compact markdown with exact file paths and line references when useful.
Prioritize the smallest context that lets Architect and Coder act safely.
Always state the project-relative paths you inspected.
"""


def create_explorer_agent(
    registry=None,
    provider: str = "ollama",
    model: str | None = None,
    workspace: str | None = None,
    url: str | None = None,
    transport: TransportType | None = "sse",
    telemetry=None,
    prompt_profile: str = "auto",
):
    """Create the read-only repository cartographer."""
    agent = Agent(
        card={
            "name": "explorer",
            "description": (
                "Read-only repository cartographer. Lists files, reads files, "
                "searches regexes, reports git status, and summarizes precise "
                "workspace context for coding tasks."
            ),
            "url": resolve_agent_url("explorer", url),
            "capabilities": {
                "delegation": False,
                "tool_calling": True,
                "multi_step_reasoning": True,
            },
            "tags": ["protoagent", "context", "read-only", "coding"],
        },
        transport=transport,
        registry=registry,
        llm=create_selected_llm(provider, model),
        system_prompt=with_workspace_contract(
            with_prompt_profile(
                EXPLORER_SYSTEM_PROMPT,
                "explorer",
                provider,
                model,
                prompt_profile,
            ),
            workspace,
            "Explorer",
        ),
        storage=conversation_storage("explorer"),
        state=["conversation"],
        telemetry=telemetry,
        logger=QUIET_LOGGER,
        policy=CapabilityPolicy(
            {
                "llm.history.compact": "allow",
                "state.compact": "allow",
                "state.describe": "allow",
                "state.reset": "allow",
                "workspace.read": "allow",
            },
            default_effect="deny",
        ),
        verbosity=0,
    )
    set_transport_timeout(agent.transport, 600)

    @agent.tool(
        name="read_file",
        description="Read a UTF-8 text file with line numbers.",
        capabilities=["workspace.read"],
    )
    def read_file(path: str) -> dict[str, Any]:
        return tools.read_file(path, workspace)

    @agent.tool(
        name="list_directory",
        description="List files and folders in a workspace path.",
        capabilities=["workspace.read"],
    )
    def list_directory(path: str = ".") -> dict[str, Any]:
        return tools.list_directory(path, workspace)

    @agent.tool(
        name="search_regex",
        description="Search workspace files using a regular expression.",
        capabilities=["workspace.read"],
    )
    def search_regex(pattern: str, path: str = ".", file_filter: str = ".*") -> dict[str, Any]:
        return tools.search_regex(pattern, path, file_filter, workspace)

    @agent.tool(
        name="get_git_status",
        description="Return git status --short for the workspace.",
        capabilities=["workspace.read"],
    )
    def get_git_status() -> dict[str, Any]:
        return tools.get_git_status(workspace)

    @agent.tool(
        name="build_context_pack",
        description="Build a Context Loom evidence pack for a focused repository question.",
        capabilities=["workspace.read"],
    )
    def build_context_pack(query: str) -> dict[str, Any]:
        return loom_context_pack(query, workspace)

    return agent
