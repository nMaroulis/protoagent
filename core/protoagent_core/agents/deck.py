"""Agent deck assembly for the ProtoAgent core."""

from __future__ import annotations

from typing import Any

from protolink import SubagentLimits
from protolink.transport import Transport
from protolink.types import TransportType

from ..config import normalize_provider
from ..prompt_profiles import prompt_profile_status
from .architect import create_architect_agent
from .coder import create_coder_agent
from .common import AgentRuntimeAuth, create_runtime_auth
from .explorer import create_explorer_agent
from .mcp import create_mcp_agent
from .scout import create_scout_agent
from .tester import create_tester_agent
from .verifier import create_verifier_agent


def create_agent_deck(
    registry=None,
    provider: str = "ollama",
    model: str | None = None,
    workspace: str | None = None,
    urls: dict[str, str] | None = None,
    transport: TransportType | Transport | None = "sse",
    approval_handler=None,
    user_input_handler=None,
    telemetry=None,
    prompt_profile: str = "auto",
    scout_enabled: bool = False,
    tester_enabled: bool = True,
    mcp_enabled: bool = False,
    mcp_servers: dict | None = None,
    auth: AgentRuntimeAuth | None = None,
    checkpoints=None,
    authorization=None,
    attempt=None,
    single_agent: bool = False,
    local_children: bool = False,
    max_children: int = 32,
    run_contract=None,
) -> dict[str, Any]:
    """Create the ProtoLink agent deck using the selected LLM config.

    Every LLM-capable agent receives its own LLM instance configured with the
    same provider/model. Architect retains conversation memory; Explorer and
    Coder are task-local workers. Verifier is a tool-only command worker.
    Tester, Scout and the tool-only MCP broker are constructed only when
    their optional-agent settings are enabled. MCP connects only on approved calls.
    When run_contract is supplied, conversation constructs only Architect;
    questions omit coding/check workers and explicit verification omits Coder.
    """
    provider = normalize_provider(provider)
    urls = urls or {}
    auth = auth or create_runtime_auth()
    record = attempt.record if attempt is not None else None
    conversational = run_contract is not None and run_contract.task_kind == "direct-answer"
    writing = run_contract is None or run_contract.requires_write
    verifying = writing or (run_contract and run_contract.task_kind == "workspace-verification")
    explorer = (
        None
        if conversational
        else create_explorer_agent(
            registry=registry,
            provider=provider,
            model=model,
            workspace=workspace,
            url=urls.get("explorer"),
            record=record if verifying else None,
            transport=transport,
            telemetry=telemetry,
            prompt_profile=prompt_profile,
            authenticator=auth.authenticator,
            credentials=auth.credentials,
        )
    )
    coder = (
        create_coder_agent(
            single_agent=single_agent,
            checkpoints=checkpoints,
            authorization=authorization,
            attempt=attempt,
            registry=registry,
            provider=provider,
            model=model,
            workspace=workspace,
            url=urls.get("coder"),
            transport=transport,
            approval_handler=approval_handler,
            telemetry=telemetry,
            prompt_profile=prompt_profile,
            authenticator=auth.authenticator,
            credentials=auth.credentials,
        )
        if writing
        else None
    )
    scout = (
        create_scout_agent(
            registry=registry,
            provider=provider,
            model=model,
            url=urls.get("scout"),
            transport=transport,
            telemetry=telemetry,
            prompt_profile=prompt_profile,
            authenticator=auth.authenticator,
            credentials=auth.credentials,
        )
        if scout_enabled and not conversational
        else None
    )
    deck = {
        **({"explorer": explorer} if explorer is not None else {}),
        **({"coder": coder} if coder is not None else {}),
        **(
            {
                "tester": create_tester_agent(
                    registry=registry,
                    provider=provider,
                    model=model,
                    workspace=workspace,
                    url=urls.get("tester"),
                    transport=transport,
                    telemetry=telemetry,
                    prompt_profile=prompt_profile,
                    authenticator=auth.authenticator,
                    credentials=auth.credentials,
                    record=record,
                )
            }
            if tester_enabled and writing
            else {}
        ),
        **(
            {
                "verifier": create_verifier_agent(
                    registry=registry,
                    workspace=workspace,
                    url=urls.get("verifier"),
                    transport=transport,
                    approval_handler=approval_handler,
                    telemetry=telemetry,
                    authenticator=auth.authenticator,
                    credentials=auth.credentials,
                    authorization=authorization,
                    attempt=attempt,
                )
            }
            if verifying
            else {}
        ),
    }
    if scout is not None:
        deck["scout"] = scout
    if mcp_enabled and not conversational:
        deck["mcp"] = create_mcp_agent(
            servers=mcp_servers or {},
            registry=registry,
            url=urls.get("mcp"),
            transport=transport,
            approval_handler=approval_handler,
            telemetry=telemetry,
            authenticator=auth.authenticator,
            credentials=auth.credentials,
            authorization=authorization,
            attempt=attempt,
        )
    deck["architect"] = create_architect_agent(
        attempt=attempt,
        registry=registry,
        provider=provider,
        model=model,
        workspace=workspace,
        url=urls.get("architect"),
        record=record,
        transport=transport,
        telemetry=telemetry,
        prompt_profile=prompt_profile,
        scout_enabled=scout_enabled,
        tester_enabled=tester_enabled,
        mcp_enabled=mcp_enabled,
        authenticator=auth.authenticator,
        credentials=auth.credentials,
        subagents=list(deck.values()) if local_children and not single_agent else (),
        subagent_limits=SubagentLimits(
            max_children=max_children, max_concurrency=1, max_depth=1, background=False
        ),
        user_input_handler=user_input_handler,
        run_contract=run_contract,
    )
    resolved_profile = prompt_profile_status(
        {"agent_prompt_profile": prompt_profile}, provider=provider, model=model
    )["resolved"]
    if local_children and not single_agent and resolved_profile == "small" and len(deck) > 1:
        from ..worker_interface import install_worker_interface

        install_worker_interface(deck["architect"])
    return deck


def agent_manifest(
    profile: dict[str, Any] | None = None,
    *,
    scout_enabled: bool = False,
    tester_enabled: bool = True,
    mcp_enabled: bool = False,
) -> dict[str, Any]:
    """Return the visible runtime architecture and worker manifest."""
    profile = profile or prompt_profile_status({"active_provider": "ollama", "providers": {}})
    profile_fields = {
        "prompt_profile": str(profile.get("resolved", "")),
        "prompt_profile_label": str(profile.get("label", "")),
    }
    return {
        "architecture": {
            "kernel": "ProtoLink runtime kernel",
            "controller": "architect",
            "worker_interface": (
                "Direct typed worker calls for small local runs; native delegation for other profiles/transports"
                if profile.get("resolved") == "small"
                else "Native ProtoLink worker delegation"
            ),
            "stateful": [
                "architect conversation memory",
                "Context Loom workspace index",
                "runtime TaskRecord and frozen check plan",
                "RunContext, RunRecorder, policy, approvals, reports",
            ],
            "stateless": [
                "explorer",
                "coder",
                "verifier",
                *(["tester"] if tester_enabled else []),
                *(["scout"] if scout_enabled else []),
                *(["mcp"] if mcp_enabled else []),
            ],
            "optional": ["tester", "scout", "mcp"],
            "contract": (
                "RunContract selects each turn's workers, tools, context and native permission ceilings. "
                "Write tasks require native execution receipts and application checks."
            ),
            "flow": [
                "RunContract",
                "Conversation, repository question, verification or editing route",
                "Context Loom evidence for repository work",
                "TaskRecord criteria and checks for editing/verification",
                "ProtoLink API-key auth",
                "Architect controller",
                "Native ask_user questions and same-run continuation",
                "Owned local children by default; explicit transport mesh available",
                "Stateless specialist workers",
                "Shared root budgets; sequential children; depth one",
                *(["Optional Scout web research"] if scout_enabled else []),
                *(["Optional MCP tool broker"] if mcp_enabled else []),
                "ProtoLink policy gate",
                "RunReport",
            ],
        },
        "agents": [
            {
                "name": "architect",
                "role": "stateful controller",
                "memory": "protoagent-architect",
                "persistence": "durable conversation memory",
                "state": "stateful",
                "contract": "routes by RunContract; manages bounded source packets and task state, never writes",
                "tools": (
                    [
                        "task_status",
                        "plan_task",
                        "ask_user",
                        "read_context_artifact",
                        "read_file",
                        "list_directory",
                        "search_regex",
                        "coder",
                        "run_check",
                        "execute_command",
                        *(["tester"] if tester_enabled else []),
                        *(["web_search", "fetch_url"] if scout_enabled else []),
                        *(
                            ["list_mcp_tools", "mcp_tool_schema", "call_mcp_tool"]
                            if mcp_enabled
                            else []
                        ),
                    ]
                    if profile.get("resolved") == "small"
                    else ["task_status", "plan_task", "worker_packet", "ask_user"]
                ),
                "enabled": True,
                "optional": False,
                **profile_fields,
            },
            {
                "name": "explorer",
                "role": "stateless context worker",
                "memory": "task-local",
                "persistence": "no durable conversation state",
                "state": "stateless",
                "contract": "returns read-only evidence for the current task",
                "tools": [
                    "build_context_pack",
                    "read_file",
                    "list_directory",
                    "search_regex",
                    "get_git_status",
                ],
                "enabled": True,
                "optional": False,
                **profile_fields,
            },
            {
                "name": "coder",
                "role": "stateless write worker",
                "memory": "task-local",
                "persistence": "no durable conversation state",
                "state": "stateless",
                "contract": "prepares RunAction diff artifacts behind approval",
                "tools": [
                    "read_file",
                    "edit_file",
                    "create_file",
                    "replace_file",
                    "preview_change",
                    "restore_change",
                    "task_status",
                    "report_task",
                ],
                "enabled": True,
                "optional": False,
                **profile_fields,
            },
            {
                "name": "tester",
                "role": "read-only test designer",
                "memory": "task-local",
                "persistence": "no durable conversation state",
                "state": "stateless",
                "enabled": tester_enabled,
                "optional": True,
                "contract": "proposes criteria and regression cases; cannot write, execute or certify success",
                "tools": [
                    "read_file",
                    "search_regex",
                    "build_context_pack",
                    "task_status",
                    "report_task",
                ],
                **profile_fields,
            },
            {
                "name": "verifier",
                "role": "stateless verification worker",
                "memory": "none",
                "persistence": "no model or durable conversation state",
                "state": "stateless",
                "contract": "approved process.execute with native budgets, output limits and cancellation",
                "tools": ["run_check", "execute_command"],
                "enabled": True,
                "optional": False,
                "prompt_profile": "not-applicable",
                "prompt_profile_label": "Tool-only (no LLM)",
            },
            {
                "name": "mcp",
                "role": "tool-only MCP broker",
                "memory": "task-local",
                "persistence": "no durable conversation state",
                "state": "stateless",
                "enabled": mcp_enabled,
                "optional": True,
                "contract": "lazy MCP discovery and allowlisted calls behind native approvals; external evidence cannot certify workspace checks",
                "tools": ["list_mcp_tools", "mcp_tool_schema", "call_mcp_tool"],
                "prompt_profile": "n/a",
                "prompt_profile_label": "Tool-only (no LLM)",
            },
            {
                "name": "scout",
                "role": "optional stateless web research worker",
                "memory": "none",
                "persistence": "no model and no durable conversation state",
                "state": "stateless",
                "contract": (
                    "exposes ProtoLink web_search and fetch_url directly under network.read policy"
                ),
                "tools": ["web_search", "fetch_url"],
                "enabled": scout_enabled,
                "optional": True,
                "prompt_profile": "not-applicable",
                "prompt_profile_label": "Tool-only (no LLM)",
            },
        ],
    }
