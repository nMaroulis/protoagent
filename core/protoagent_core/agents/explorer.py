"""Explorer agent factory."""

from __future__ import annotations

from typing import Any

from protolink import Agent, CapabilityPolicy
from protolink.transport import Transport
from protolink.types import TransportType

from .. import tools
from ..context import build_context_pack as loom_context_pack
from .common import (
    QUIET_LOGGER,
    create_configured_transport,
    create_selected_llm,
    resolve_agent_url,
    with_prompt_profile,
    with_workspace_contract,
)

EXPLORER_SYSTEM_PROMPT = """You are the ProtoAgent Explorer.

You are a stateless, task-local context worker. Do not rely on prior
conversation memory; use only the task, Context Loom, and read-only tools.

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
    transport: TransportType | Transport | None = "sse",
    telemetry=None,
    prompt_profile: str = "auto",
    authenticator=None,
    credentials: str | None = None,
):
    """Create the stateless read-only repository worker."""
    agent_url = resolve_agent_url("explorer", url)
    agent = Agent(
        card={
            "name": "explorer",
            "description": (
                "Stateless read-only repository worker. Lists files, reads "
                "files, searches regexes, reports git status, and summarizes "
                "precise workspace context for coding tasks."
            ),
            "url": agent_url,
            "capabilities": {
                "delegation": False,
                "tool_calling": True,
                "multi_step_reasoning": True,
            },
            "tags": ["protoagent", "context", "read-only", "coding"],
        },
        transport=create_configured_transport(
            transport,
            agent_url,
            authenticator=authenticator,
            credentials=credentials,
        ),
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
        storage=None,
        state=[],
        telemetry=telemetry,
        logger=QUIET_LOGGER,
        authenticator=authenticator,
        credentials=credentials,
        policy=CapabilityPolicy(
            {
                "workspace.read": "allow",
            },
            default_effect="deny",
        ),
        verbosity=0,
    )

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
