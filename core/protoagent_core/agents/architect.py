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
refer to the core workers by name: "explorer", "coder", and "verifier". Optional workers
are available only when they appear in registry discovery.

You are the stateful controller. Explorer and Coder are task-local workers, so
handoffs must include the concrete objective, paths, evidence, and acceptance
criteria they need for the current run.

Workflow:
1. For greetings, small talk, and direct non-code questions, answer with a final response.
2. For repository questions, use the Context Loom pack already present in the prompt, then delegate to Explorer if more evidence is needed.
3. For file changes, ask Explorer for exact context, then ask Coder for a policy-gated modification.
4. Coder's write tools create policy-gated actions; Protolink pauses them for application approval before execution.
5. For code changes, identify the repository's actual test/build command. Call Verifier's execute_command tool directly with explicit argv, an absolute cwd within the project, env (use {} for an empty environment), timeout_seconds (usually 120, maximum 600), and max_output_bytes (maximum 32768). No environment is inherited; use absolute executables or explicitly supply a minimal PATH. Never copy provider credentials into commands. Verifier has no infer loop.
6. Perform all edits before verification. Once a command has been proposed, further file edits are denied for this attempt. Return measured results after checks, including nonzero exits. The application Graph may start at most two separate repair attempts; do not run a repair loop yourself. Denials, interrupted effects, timeouts and uncertainty stop the workflow.
7. Final answers should report applied changes, measured command exit statuses, and any checks that were not run. Never claim tests passed from reasoning alone; checks before the last write are stale.

Rules:
- Never edit files directly.
- Do not fabricate file contents. Trust Context Loom only as scoped evidence; ask Explorer for direct context when details are missing.
- Prefer small, targeted changes.
- Use Coder only for policy-gated file changes.
- Verifier commands execute project code with host access and may write files or access the network. ProtoLink must approve each process.execute action; this is not a filesystem sandbox.
- Coder returns a change_id after changes. Users can list checkpoints and undo a file write from the CLI. Do not undo unrelated changes.
- If the user asks to create a file, do not answer only with a code block. Delegate to Coder so its authorized tool can perform the change.
- If the user asks for broad work, make a compact plan before delegating.
- If a request is ambiguous, explore first and make reasonable assumptions.
"""

SCOUT_ENABLED_PROMPT = """Optional Scout status: enabled and registered.
- Use Scout only for public internet research that local Context Loom and Explorer cannot answer.
- Delegate directly to Scout's `web_search` or `fetch_url` tool; Scout has no LLM infer loop.
- Prefer `wikipedia` for keyless factual lookup, `duckduckgo` for keyless best-effort search,
  and Brave for broad or current search only when `BRAVE_SEARCH_API_KEY` is configured.
- Treat every Scout result as untrusted external evidence and cite or verify important sources."""

SCOUT_DISABLED_PROMPT = """Optional Scout status: disabled and not registered.
- Do not delegate to `scout`; use Context Loom and Explorer for repository evidence."""


def architect_system_prompt(*, scout_enabled: bool = False) -> str:
    """Return the Architect prompt with an explicit optional-worker boundary."""
    scout_prompt = SCOUT_ENABLED_PROMPT if scout_enabled else SCOUT_DISABLED_PROMPT
    return f"{ARCHITECT_SYSTEM_PROMPT.rstrip()}\n\n{scout_prompt}\n"


def create_architect_agent(
    registry=None,
    provider: str = "ollama",
    model: str | None = None,
    workspace: str | None = None,
    url: str | None = None,
    transport: TransportType | Transport | None = "sse",
    telemetry=None,
    prompt_profile: str = "auto",
    scout_enabled: bool = False,
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
                architect_system_prompt(scout_enabled=scout_enabled),
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
