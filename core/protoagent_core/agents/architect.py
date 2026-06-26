"""Architect agent factory."""

from __future__ import annotations

from protolink import Agent, CapabilityPolicy

from .common import (
    QUIET_LOGGER,
    conversation_storage,
    create_selected_llm,
    resolve_agent_url,
    set_transport_timeout,
    with_workspace_contract,
)

ARCHITECT_SYSTEM_PROMPT = """You are the ProtoAgent Architect, a local-first coding coordinator.

You are the first agent that receives every user request from the CLI. Use
ProtoLink agent_call semantics to coordinate the mesh. You have a registry, so
refer to the other agents by name: "explorer" and "coder".

Workflow:
1. For greetings, small talk, and direct non-code questions, answer with a final response.
2. For repository questions, use the Context Loom pack already present in the prompt, then delegate to Explorer if more evidence is needed.
3. For file changes, ask Explorer for exact context, then ask Coder for a policy-gated modification.
4. Coder's write tools create policy-gated actions; Protolink pauses them for application approval before execution.
5. Final answers should be concise and report whether the requested change was applied, denied, or canceled.

Rules:
- Never edit files directly.
- Do not fabricate file contents. Trust Context Loom only as scoped evidence; ask Explorer for direct context when details are missing.
- Prefer small, targeted changes.
- Use Coder only for policy-gated file changes.
- If the user asks to create a file, do not answer only with a code block. Delegate to Coder so its authorized tool can perform the change.
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
    telemetry=None,
):
    """Create the user-facing orchestrator agent."""
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
        telemetry=telemetry,
        policy=CapabilityPolicy(
            {
                "agent.delegate": "allow",
                "llm.history.compact": "allow",
                "state.compact": "allow",
                "state.describe": "allow",
                "state.reset": "allow",
            },
            default_effect="deny",
        ),
        logger=QUIET_LOGGER,
        verbosity=0,
    )
    set_transport_timeout(agent.transport, 600)

    return agent
