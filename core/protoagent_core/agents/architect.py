"""Architect agent factory."""

from __future__ import annotations

import os
from typing import Any

from .common import (
    QUIET_LOGGER,
    conversation_storage,
    create_selected_llm,
    record_side_effect,
    resolve_agent_url,
    set_transport_timeout,
    session_aware_agent_class,
    with_workspace_contract,
)

ARCHITECT_SYSTEM_PROMPT = """You are the ProtoAgent Architect, a local-first coding coordinator.

You are the first agent that receives every user request from the CLI. Use
ProtoLink agent_call semantics to coordinate the mesh. You have a registry, so
refer to the other agents by name: "explorer" and "coder".

Workflow:
1. For greetings, small talk, and direct non-code questions, answer with a final response.
2. For repository questions, delegate to Explorer first for read-only context.
3. For file changes, ask Explorer for exact context, then ask Coder for diff synthesis.
4. When a proposed write is ready, create an approval checkpoint with request_user_approval.
5. Final answers should be concise and mention whether approval is required.

Rules:
- Never edit files directly.
- Do not fabricate file contents. Ask Explorer for context first.
- Prefer small, targeted changes.
- Use Coder only for diffs and new-file proposals.
- If the user asks to create a file, do not answer only with a code block. Delegate to Coder so a pending file action is produced.
- If the user asks for broad work, make a compact plan before delegating.
- If a request is ambiguous, explore first and make reasonable assumptions.
"""


def create_architect_agent(
    registry=None,
    provider: str = "ollama",
    model: str | None = None,
    workspace: str | None = None,
    url: str | None = None,
    transport: str = "sse",
    side_effects: list[dict[str, Any]] | None = None,
):
    """Create the user-facing orchestrator agent."""
    Agent = session_aware_agent_class()

    agent = Agent(
        card={
            "name": "architect",
            "description": (
                "User-facing ProtoAgent orchestrator. Receives CLI tasks, "
                "discovers specialist agents through the registry, delegates "
                "repository exploration to Explorer, and delegates diff "
                "synthesis to Coder."
            ),
            "url": resolve_agent_url("architect", url),
            "capabilities": {
                "delegation": True,
                "tool_calling": True,
                "multi_step_reasoning": True,
            },
            "tags": ["protoagent", "orchestrator", "coding"],
        },
        transport=transport,
        registry=registry,
        llm=create_selected_llm(provider, model),
        system_prompt=with_workspace_contract(ARCHITECT_SYSTEM_PROMPT, workspace, "Architect"),
        storage=conversation_storage("architect"),
        state=["conversation"],
        logger=QUIET_LOGGER,
        verbosity=0,
    )
    set_transport_timeout(agent.transport, 600)

    @agent.tool(
        name="request_user_approval",
        description="Create a frontend approval checkpoint for proposed file changes.",
        input_schema={"summary": str, "file_target": str, "diff": str},
    )
    def request_user_approval(summary: str, file_target: str, diff: str) -> dict[str, Any]:
        payload = {
            "success": True,
            "summary": summary,
            "file_target": file_target,
            "diff": diff,
            "requires_approval": True,
            "workspace": workspace or os.getenv("PROTOAGENT_WORKSPACE", os.getcwd()),
            "action": {
                "type": "approval_checkpoint",
                "path": file_target,
                "summary": summary,
                "diff": diff,
            },
        }
        record_side_effect(side_effects, {"source": "architect", **payload})
        return payload

    return agent
