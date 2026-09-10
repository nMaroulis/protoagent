"""Tool-only verification agent using ProtoLink authorization and tool dispatch."""

from __future__ import annotations

import shlex
from collections.abc import Callable
from typing import Any

from protolink import Agent, Artifact, CapabilityPolicy, Part, RunAction, RunContext

from ..verification import VerificationEvidence, run_command, validate_command
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
    evidence: VerificationEvidence | None = None,
    on_output: Callable[[str], None] | None = None,
):
    """Expose approved test/build execution without an LLM or conversation state.

    Architect delegates directly to ``run_command`` through ProtoLink. The
    separate shell.execute capability always requires a typed user approval.
    """
    agent_url = resolve_agent_url("verifier", url)
    agent = Agent(
        card={
            "name": "verifier",
            "description": "Run a test, build, or lint command and return actual output and exit status. Call run_command directly; no infer loop.",
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
        policy=CapabilityPolicy({"shell.execute": "require_approval"}, default_effect="deny"),
        approval_handler=approval_handler,
    )

    def prepare(arguments: dict[str, Any], context: RunContext) -> RunAction:
        """Present the exact command and directory before ProtoLink authorizes it."""
        argv, cwd, timeout = validate_command(
            arguments["argv"],
            arguments.get("cwd", "."),
            arguments.get("timeout_seconds", 120),
            workspace,
        )
        command = shlex.join(argv)
        return RunAction(
            kind="shell.execute",
            name="run_command",
            description=f"Run {command} in {cwd} (timeout {timeout}s; host access)",
            payload={"arguments": {"argv": argv, "cwd": cwd, "timeout_seconds": timeout}},
            metadata={"path": cwd, "workspace_uri": context.workspace_uri},
        ).with_artifacts(
            [
                Artifact(
                    kind="preview",
                    name="Command preview",
                    media_type="text/plain",
                    parts=[
                        Part.text(
                            f"{command}\nDirectory: {cwd}\nTimeout: {timeout}s\nRuns project code with host access; may write files or use the network."
                        )
                    ],
                )
            ]
        )

    @agent.tool(
        name="run_command",
        description="Run approved argv for tests, builds, or linting. Returns exit_code, output, timed_out, and truncated. No shell expansion; cwd stays inside the project.",
        capabilities=["shell.execute"],
        action_builder=prepare,
    )
    async def verify(argv: list[str], cwd: str = ".", timeout_seconds: int = 120) -> dict[str, Any]:
        """Execute only after ProtoLink's policy authorizer approves this command."""
        revision = evidence.revision() if evidence else 0
        result = await run_command(
            argv, cwd, timeout_seconds, workspace=workspace, on_output=on_output
        )
        if evidence:
            evidence.record(result, revision)
        return result

    return agent
