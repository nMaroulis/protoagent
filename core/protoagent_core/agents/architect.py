"""Architect agent factory."""

from __future__ import annotations

from protolink import Agent, CapabilityPolicy
from protolink.transport import Transport
from protolink.types import TransportType

from .common import (
    QUIET_LOGGER,
    conversation_storage,
    create_configured_transport,
    create_selected_llm,
    resolve_agent_url,
    with_prompt_profile,
    with_workspace_contract,
)

ARCHITECT_SYSTEM_PROMPT = """You are the ProtoAgent Architect, a local-first coding coordinator.

You are the first agent that receives every user request from the CLI. Use
ProtoLink agent_call semantics to coordinate the mesh. You have a registry, so
refer to the other agents by name: "explorer" and "coder".

You are the stateful controller. Explorer and Coder are task-local workers, so
handoffs must include the concrete objective, paths, evidence, and acceptance
criteria they need for the current run.

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
    transport: TransportType | Transport | None = "sse",
    telemetry=None,
    prompt_profile: str = "auto",
    authenticator=None,
    credentials: str | None = None,
):
    """Create the stateful user-facing controller agent."""
    agent_url = resolve_agent_url("architect", url)
    agent = Agent(
        card={
            "name": "architect",
            "description": (
                "Stateful ProtoAgent controller. Receives CLI tasks, discovers "
                "stateless specialist workers through the registry, delegates "
                "repository exploration to Explorer, and delegates diff synthesis "
                "to Coder."
            ),
            "url": agent_url,
            "capabilities": {
                "delegation": True,
                "tool_calling": True,
                "multi_step_reasoning": True,
            },
            "tags": ["protoagent", "orchestrator", "coding"],
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
                ARCHITECT_SYSTEM_PROMPT,
                "architect",
                provider,
                model,
                prompt_profile,
            ),
            workspace,
            "Architect",
        ),
        storage=conversation_storage("architect"),
        state=["conversation"],
        telemetry=telemetry,
        authenticator=authenticator,
        credentials=credentials,
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

    return agent
