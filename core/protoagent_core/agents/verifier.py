"""Tool-only verification worker backed entirely by ProtoLink's process tool."""

from __future__ import annotations

from protolink import Agent
from protolink.tools.builtins import process_tool

from ..editing import check_tool, process_tool_with_identity
from ..runtime_policy import WorkspacePolicy
from ..tools import workspace_root
from .common import QUIET_LOGGER, create_configured_transport, resolve_agent_url


def create_verifier_agent(
    registry=None,
    workspace: str | None = None,
    url: str | None = None,
    transport="sse",
    approval_handler=None,
    telemetry=None,
    authenticator=None,
    credentials: str | None = None,
    authorization=None,
    attempt=None,
):
    """Register execute_command without launching a process or constructing an LLM.

    The native prepared argv, absolute cwd, explicit env and output/time limits
    are approved under process.execute. ProtoLink owns budgets and cancellation.
    The local backend runs on the host, without sandbox isolation.
    """
    agent_url = resolve_agent_url("verifier", url)
    agent = Agent(
        card={
            "name": "verifier",
            "description": "Run required repository checks with run_check(check_id, phase=baseline|verify). execute_command is for approved preparation; arbitrary commands do not prove verification. No infer loop.",
            "url": agent_url,
            "capabilities": {
                "streaming": True,
                "delegation": False,
                "tool_calling": True,
                "multi_step_reasoning": False,
            },
            "tags": ["protoagent", "verification"],
        },
        transport=create_configured_transport(
            transport, agent_url, authenticator=authenticator, credentials=credentials
        ),
        registry=registry,
        llm=None,
        storage=None,
        state=[],
        expose_chat=False,
        telemetry=telemetry,
        logger=QUIET_LOGGER,
        verbosity=0,
        authenticator=authenticator,
        credentials=credentials,
        policy=WorkspacePolicy(
            {"process.execute": "require_approval"},
            workspace=workspace_root(workspace),
            authorization=authorization,
            attempt=attempt,
        ),
        approval_handler=approval_handler,
    )
    native = process_tool_with_identity(
        process_tool(max_timeout_seconds=600, max_output_bytes=32768)
    )
    agent.add_tool(native)
    if attempt is not None and attempt.record is not None:
        agent.add_tool(check_tool(native, attempt.record))
    return agent
