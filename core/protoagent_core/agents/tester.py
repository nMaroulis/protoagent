"""Tester agent factory."""

from __future__ import annotations

from typing import Any

from protolink import Agent, CapabilityPolicy
from protolink.transport import Transport
from protolink.types import TransportType

from .. import tools
from ..context import build_context_pack as loom_context_pack
from ..task_record import add_task_tools
from .common import (
    QUIET_LOGGER,
    create_configured_transport,
    create_selected_llm,
    resolve_agent_url,
    with_prompt_profile,
    with_workspace_contract,
)

TESTER_SYSTEM_PROMPT = """You are ProtoAgent Tester, a task-local read-only test designer.
Inspect the requested behavior, relevant source and existing tests. Read task_status
for repository check IDs. Return a small test plan: acceptance criteria, selected
check IDs, regression cases and test files Coder should change. If a failure is
provided, distinguish a code failure from an environment problem using evidence.
For bootstrap_checks, propose real stdlib unittest regressions in root test_*.py
files; the predefined runner is already captured. A baseline with zero tests is
missing coverage, not proof of correctness. Include test paths in your handoff.
Use report_task(done, summary) when the plan is ready, needs_context when evidence
is missing, or blocked when no meaningful check exists. You cannot edit or execute
commands. Never claim tests passed. Prefer a regression that fails before the fix
and passes afterwards. Keep existing tests intact and do not weaken assertions.
"""


def create_tester_agent(
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
    record=None,
):
    """Create the stateless read-only repository worker."""
    agent_url = resolve_agent_url("tester", url)
    agent = Agent(
        card={
            "name": "tester",
            "description": (
                "Read-only regression designer. Inspects source and tests, selects "
                "repository check IDs, proposes edge cases and classifies failures."
            ),
            "url": agent_url,
            "capabilities": {
                "streaming": True,
                "delegation": False,
                "tool_calling": True,
                "multi_step_reasoning": True,
            },
            "tags": ["protoagent", "testing", "read-only", "coding"],
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
                TESTER_SYSTEM_PROMPT,
                "tester",
                provider,
                model,
                prompt_profile,
            ),
            workspace,
            "Tester",
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
                "task.manage": "allow",
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
    def read_file(path: str, start_line: int = 1, end_line: int | None = None) -> dict[str, Any]:
        result = tools.read_file(
            path,
            workspace,
            start_line=start_line,
            end_line=end_line,
            max_chars=4000 if prompt_profile == "small" else 8192,
        )
        if record and result.get("success"):
            record.source_paths.add(str(tools.safe_path(path, workspace)))
        return result

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

    add_task_tools(agent, record, "tester")
    return agent
