"""Tool-only verification worker backed entirely by ProtoLink's process tool."""

from __future__ import annotations

from protolink import Agent
from protolink.tools.builtins import process_tool

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
            "description": "Execute an approved test/build command. Call execute_command directly with argv, absolute cwd, explicit env, timeout_seconds and max_output_bytes; no infer loop.",
            "url": agent_url,
            "capabilities": {
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
    agent.add_tool(process_tool(max_timeout_seconds=600, max_output_bytes=32768))
    return agent
