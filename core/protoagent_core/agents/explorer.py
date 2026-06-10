"""Explorer agent factory."""

from __future__ import annotations

from typing import Any

from .. import tools
from .common import QUIET_LOGGER, create_selected_llm, resolve_agent_url

EXPLORER_SYSTEM_PROMPT = """You are the ProtoAgent Explorer.

Build dense context maps for coding tasks. You may read files, list directories,
search with regexes, and inspect git status. You must never modify files or
execute arbitrary shell commands.

Return compact markdown with exact file paths and line references when useful.
Prioritize the smallest context that lets Architect and Coder act safely.
"""


def create_explorer_agent(
    registry=None,
    provider: str = "ollama",
    model: str | None = None,
    workspace: str | None = None,
    url: str | None = None,
):
    """Create the read-only repository cartographer."""
    from protolink.agents import Agent

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
        transport="http",
        registry=registry,
        llm=create_selected_llm(provider, model),
        system_prompt=EXPLORER_SYSTEM_PROMPT,
        logger=QUIET_LOGGER,
        verbosity=0,
    )
    agent.transport.timeout = 600

    @agent.tool(name="read_file", description="Read a UTF-8 text file with line numbers.")
    def read_file(path: str) -> dict[str, Any]:
        return tools.read_file(path, workspace)

    @agent.tool(name="list_directory", description="List files and folders in a workspace path.")
    def list_directory(path: str = ".") -> dict[str, Any]:
        return tools.list_directory(path, workspace)

    @agent.tool(
        name="search_regex",
        description="Search workspace files using a regular expression.",
        input_schema={"pattern": str, "path": str, "file_filter": str},
    )
    def search_regex(pattern: str, path: str = ".", file_filter: str = ".*") -> dict[str, Any]:
        return tools.search_regex(pattern, path, file_filter, workspace)

    @agent.tool(name="get_git_status", description="Return git status --short for the workspace.")
    def get_git_status() -> dict[str, Any]:
        return tools.get_git_status(workspace)

    return agent

