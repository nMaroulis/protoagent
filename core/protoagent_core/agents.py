"""ProtoAgent protolink agent topology.

The whitepaper topology is:

* Architect: user-facing orchestrator.
* Explorer: read-only repository cartographer.
* Coder: synthesis agent that prepares pending changes.
"""

from __future__ import annotations

import os
from typing import Any

from . import tools
from .config import normalize_provider
from .llm import create_llm_from_config

ARCHITECT_SYSTEM_PROMPT = """You are the ProtoAgent Architect, a local-first coding coordinator.

Coordinate specialist agents using protolink agent_call semantics. Never edit files
directly. First ask Explorer for repository context, then ask Coder for precise
pending changes. All write or shell-like work must be returned as an approval
payload for the CLI.

Rules:
- Keep the user-facing response concise.
- Prefer small, targeted changes.
- Do not fabricate file contents. Ask Explorer for context first.
- Use Coder only for diff generation and new-file proposals.
- Final answers should mention pending actions and whether approval is required.
"""

EXPLORER_SYSTEM_PROMPT = """You are the ProtoAgent Explorer.

Build dense context maps for coding tasks. You may read, list, search, and inspect
git status, but you must never modify files or execute arbitrary shell commands.
Return compact markdown with exact file paths and line references when useful.
"""

CODER_SYSTEM_PROMPT = """You are the ProtoAgent Coder.

Given a user objective and Explorer context, produce exact file modifications.
Use your tools to generate unified diffs or new-file proposals. Do not write to
disk. Return pending actions that require frontend approval.
"""


def create_architect_agent(
    registry=None,
    provider: str = "ollama",
    model: str | None = None,
    workspace: str | None = None,
):
    from protolink.agents import Agent

    llm = create_llm_from_config(normalize_provider(provider), model)
    agent = Agent(
        card={
            "name": "architect",
            "description": (
                "User-facing ProtoAgent orchestrator. Delegates exploration to "
                "Explorer and diff synthesis to Coder."
            ),
            "url": os.getenv("ARCHITECT_AGENT_URL", "http://localhost:8010"),
        },
        transport="http",
        registry=registry,
        llm=llm,
        system_prompt=ARCHITECT_SYSTEM_PROMPT,
        verbosity=1,
    )

    @agent.tool(
        name="request_user_approval",
        description="Create a frontend approval checkpoint for proposed file changes.",
        input_schema={"summary": str, "file_target": str, "diff": str},
    )
    def request_user_approval(summary: str, file_target: str, diff: str) -> dict[str, Any]:
        return {
            "success": True,
            "summary": summary,
            "file_target": file_target,
            "diff": diff,
            "requires_approval": True,
            "workspace": workspace or os.getenv("PROTOAGENT_WORKSPACE", os.getcwd()),
        }

    return agent


def create_explorer_agent(registry=None, workspace: str | None = None):
    from protolink.agents import Agent

    agent = Agent(
        card={
            "name": "explorer",
            "description": (
                "Read-only repository cartographer. Lists files, reads files, "
                "searches regexes, and reports git status."
            ),
            "url": os.getenv("EXPLORER_AGENT_URL", "http://localhost:8020"),
        },
        transport="http",
        registry=registry,
        system_prompt=EXPLORER_SYSTEM_PROMPT,
        verbosity=1,
    )

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


def create_coder_agent(
    registry=None,
    provider: str = "ollama",
    model: str | None = None,
    workspace: str | None = None,
):
    from protolink.agents import Agent

    llm = create_llm_from_config(normalize_provider(provider), model)
    agent = Agent(
        card={
            "name": "coder",
            "description": (
                "Diff synthesis agent. Generates unified diffs and new-file "
                "proposals without writing to disk."
            ),
            "url": os.getenv("CODER_AGENT_URL", "http://localhost:8030"),
        },
        transport="http",
        registry=registry,
        llm=llm,
        system_prompt=CODER_SYSTEM_PROMPT,
        verbosity=1,
    )

    @agent.tool(
        name="generate_unified_diff",
        description="Generate a unified diff for replacing a file with updated content.",
        input_schema={"path": str, "updated_content": str, "original_content": str},
    )
    def generate_unified_diff(
        path: str,
        updated_content: str,
        original_content: str | None = None,
    ) -> dict[str, Any]:
        return tools.generate_unified_diff(path, updated_content, original_content, workspace)

    @agent.tool(
        name="create_new_file",
        description="Prepare a new file creation as a pending approval action.",
        input_schema={"path": str, "content": str},
    )
    def create_new_file(path: str, content: str) -> dict[str, Any]:
        return tools.create_new_file(path, content, workspace)

    return agent


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
