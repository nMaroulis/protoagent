"""ProtoLink runtime mesh for the CLI core."""

from __future__ import annotations

import asyncio
import os
import socket
from contextlib import suppress
from pathlib import Path
from typing import Any, Literal

from .agents import create_agent_deck
from .agents.common import (
    QUIET_LOGGER,
    RUNTIME_SCOPES,
    create_configured_transport,
    create_runtime_auth,
)
from .config import load_config, normalize_provider, optional_agent_enabled, provider_config
from .history import compact_agent_histories_for_run
from .prompt_profiles import prompt_profile_status
from .run_contracts import infer_run_contract
from .runtime_bridge import RuntimeBridge
from .verification import validate_completion, verification_summary

_FALLBACK_PORT = 19100
TransportName = Literal[
    "local", "http", "websocket", "sse", "json-rpc", "sse-json-rpc", "grpc", "runtime"
]


def run_selected_model(
    prompt: str,
    workspace: str | None = None,
    session_id: str | None = None,
    progress_path: str | None = None,
    user_prompt: str | None = None,
) -> dict[str, Any]:
    """Run the selected model through the ProtoLink Architect agent.

    AgentGroup owns the embedded lifecycle. A native RunHandle executes a
    bounded application Graph; Architect delegates to owned local children by
    default, or through explicitly selected ProtoLink transports and Registry.
    """
    config = load_config()
    provider = normalize_provider(config.get("active_provider", "ollama"))
    cfg = provider_config(provider, config)
    model = cfg.get("model", "")
    if not model:
        raise RuntimeError(f"No model selected for provider '{provider}'")
    profile = prompt_profile_status(config, provider=provider, model=str(model))

    bridge = RuntimeBridge(progress_path)
    run_state = {}
    try:
        with bridge.capture_console():
            return asyncio.run(
                _run_agent_deck(
                    prompt,
                    provider,
                    model,
                    workspace,
                    session_id,
                    bridge,
                    profile,
                    user_prompt=user_prompt,
                    scout_enabled=optional_agent_enabled("scout", config),
                    tester_enabled=optional_agent_enabled("tester", config),
                    mcp_enabled=optional_agent_enabled("mcp", config),
                    mcp_config=config,
                    run_state=run_state,
                )
            )
    except Exception as exc:
        handle = run_state.get("handle")
        if handle is None:
            raise
        # A failure in application validation/presentation or cleanup after
        # submission cannot establish that the effects did not happen.
        report = handle.report
        return bridge.redaction.redact(
            {
                "provider": provider,
                "model": model,
                "responder": "architect",
                "status": "uncertain",
                "answer": f"Run interrupted after submission: {exc}. Inspect effects before requesting new work.",
                "events": [],
                "run_events": [event.to_dict() for event in report.events],
                "run_report": report.to_dict(),
                "run_context": handle.context.to_dict(),
                "approval_requests": bridge.approval_requests,
                "approval_decisions": bridge.approval_decisions,
                "diffs": [],
                "targets": [],
            }
        )
    finally:
        bridge.cleanup()


async def _run_agent_deck(
    prompt: str,
    provider: str,
    model: str,
    workspace: str | None,
    session_id: str | None,
    bridge: RuntimeBridge,
    prompt_profile: dict[str, Any],
    user_prompt: str | None = None,
    scout_enabled: bool = False,
    tester_enabled: bool = True,
    mcp_enabled: bool = False,
    mcp_config: dict[str, Any] | None = None,
    run_state: dict[str, Any] | None = None,
    architecture: str = "deck",
) -> dict[str, Any]:
    """Own the embedded mesh with AgentGroup and consume native RunHandle results."""
    from protolink import (
        Agent,
        AgentGroup,
        ApprovalBroker,
        CapabilityPolicy,
        RunBudget,
        RunContext,
        RunReport,
        Task,
    )
    from protolink.discovery import Registry
    from protolink.storage import SQLiteRunStore, SQLiteStorage

    from . import config
    from .approvals import approval_mode
    from .checkpoints import checkpoint_store, private_file, workspace_writer
    from .mcp import server_settings
    from .request_budget import configure_agent_context
    from .runtime_policy import AttemptState, RunAuthorization
    from .runtime_storage import output_redaction
    from .streaming import LiveOutput
    from .task_record import TaskRecord
    from .workflow import CodingWorkflow

    project = str(Path(workspace or os.getenv("PROTOAGENT_WORKSPACE", os.getcwd())).resolve())
    selected_approval_mode = approval_mode(project)
    contract = infer_run_contract(user_prompt or prompt)
    context = RunContext(
        session_id=session_id,
        workspace_uri=Path(project).as_uri(),
        permissions=contract.permissions(RUNTIME_SCOPES),
        budget=_run_budget(provider, model, RunBudget),
        metadata={
            "application": "protoagent",
            "approval_mode": selected_approval_mode,
            "interface": "rust-cli",
            "prompt_profile": prompt_profile,
            "run_contract": contract.to_dict(),
            "optional_agents": {
                "tester": tester_enabled,
                "scout": scout_enabled,
                "mcp": mcp_enabled and architecture != "single",
            },
        },
    )
    context.trace_id = context.run_id
    authorization = RunAuthorization(context)
    auth = create_runtime_auth()
    redaction = output_redaction(auth.credentials)
    bridge.redaction = redaction
    events: list[str] = []
    live_updates = _streaming_enabled()
    seen_events: set[str] = set()

    def emit(message):
        events.append(message)
        bridge.emit(message)

    async def observe(handle):
        def present(event):
            # Graph attempts and the enclosing handle expose the same native
            # receipts. Deduplicate presentation across the whole application
            # run, not just one handle; full evidence remains in native reports.
            if event.event_id in seen_events:
                return
            seen_events.add(event.event_id)
            if live_updates:
                live_output.emit(event)
            data = event.to_dict(redaction_policy=redaction)
            summary = _run_event_summary(data)
            if summary:
                _append_event(events, summary, bridge if live_updates else None, run_event=data)

        async def child_observations():
            # ProtoLink 0.8.0 commits live child events to the parent's Task;
            # its local recorder can deliver them to the stream only at join.
            # This is presentation of existing evidence, never task execution.
            from protolink import RunEvent

            cursor = 0
            while True:
                records = handle.task.metadata.get("run_events", [])
                batch = records[cursor : cursor + 256]
                cursor += len(batch)
                for data in batch:
                    if data.get("delegation_id"):
                        present(RunEvent.from_dict(data))
                await asyncio.sleep(0 if cursor < len(records) else 0.025)

        observations = (
            asyncio.create_task(child_observations()) if _agent_transport() == "local" else None
        )
        try:
            async for event in handle.events():
                present(event)
        finally:
            if observations is not None:
                observations.cancel()
                with suppress(asyncio.CancelledError):
                    await observations

    with workspace_writer(project):
        store = SQLiteRunStore(
            private_file(config.CONFIG_DIR / "runs" / f"{context.run_id}.sqlite"),
            redaction_policy=redaction,
        )
        broker = ApprovalBroker(
            storage=SQLiteStorage(store.db_path, table_name="approvals", namespace=context.run_id),
            timeout_seconds=float(_runtime_timeout()),
        )
        bridge.bind(broker, authorization, redaction, approval_mode=selected_approval_mode)
        task = Task.create_tool_call(tool_name="run_workflow", args={})
        context.attach_to_task(task)
        if reason := bridge.cancel_reason():
            context.cancel(reason).attach_to_task(task)
            task.cancel(reason)
            report = RunReport.from_task(task)
            store.save_report(report)
            return {
                "provider": provider,
                "model": model,
                "responder": "architect",
                "answer": f"Task canceled before model execution: {reason}",
                "status": "canceled",
                "events": events,
                "run_events": [],
                "run_report": report.to_dict(),
                "run_context": RunContext.from_task(task).to_dict(),
                "diffs": [],
                "targets": [],
                "approval_requests": [],
                "approval_decisions": [],
            }
        checkpoints = checkpoint_store(project)
        record = TaskRecord.create(project, user_prompt or prompt, contract=contract)
        attempt = AttemptState(project, checkpoints, authorization, record=record)
        urls = _runtime_urls()
        transport = _agent_transport()
        registry_transport = (
            None
            if transport == "local"
            else create_configured_transport(
                "runtime" if transport == "runtime" else "http",
                urls["registry"],
                timeout=_runtime_timeout(),
            )
        )
        registry = (
            Registry(transport=registry_transport, verbosity=0) if registry_transport else None
        )
        deck = create_agent_deck(
            single_agent=architecture == "single",
            registry=registry,
            provider=provider,
            model=model,
            workspace=project,
            urls=urls,
            transport=None if transport == "local" else transport,
            local_children=transport == "local",
            max_children=_env_int("PROTOAGENT_MAX_CHILDREN") or 32,
            approval_handler=broker,
            user_input_handler=bridge.ask_user,
            telemetry=_trace_telemetry(redaction),
            prompt_profile=str(prompt_profile["resolved"]),
            scout_enabled=scout_enabled,
            tester_enabled=tester_enabled,
            mcp_enabled=mcp_enabled and architecture != "single",
            mcp_servers=server_settings(mcp_config)
            if mcp_enabled and architecture != "single"
            else {},
            auth=auth,
            checkpoints=checkpoints,
            authorization=authorization,
            attempt=attempt,
            run_contract=contract,
        )
        if architecture == "single" and contract.requires_write:
            from .task_record import add_task_tools

            single = deck["coder"]
            single.card.name = "architect"
            for worker in (deck["explorer"], deck["verifier"]):
                for name, tool in worker.tools.items():
                    if name not in single.tools and name != "report_task":
                        single.add_tool(tool)
            add_task_tools(single, record, "architect")
            from .user_input import add_user_input_tool

            add_user_input_tool(single, bridge.ask_user, record)
            deck = {"architect": single}
        elif architecture not in {"deck", "single"}:
            raise ValueError("Unknown evaluation architecture")
        for agent in deck.values():
            agent.run_store = store
            if agent.llm is not None:
                configure_agent_context(
                    agent,
                    record,
                    fallback_window=8192 if prompt_profile["resolved"] == "small" else None,
                    compact_protocol=prompt_profile["resolved"] == "small",
                    run_contract=contract,
                    cards=[worker.card for name, worker in deck.items() if name != "architect"]
                    if agent.card.name == "architect" and architecture != "single"
                    else (),
                )
        live_output = LiveOutput(
            bridge,
            redaction,
            json_agents={
                name
                for name, agent in deck.items()
                if agent.llm is not None and not agent.llm.supports_native_action_stream
            },
        )
        compaction_reports = await compact_agent_histories_for_run(deck.values(), session_id)
        for report in compaction_reports:
            if report.get("changed"):
                emit(f"Compacted {report['agent']} conversation history.")
        emit(
            f"Runtime: ProtoLink AgentGroup; "
            f"{'owned local children' if transport == 'local' else transport + ' worker transport'}; "
            f"{provider} / {model}."
        )
        emit(f"Project: {project}. Run: {context.run_id}. Repair limit: 2.")
        coordinator = Agent(
            execution_version="protoagent-workflow-3",
            card={
                "name": "protoagent-workflow",
                "description": "Application coding workflow",
                "url": "runtime://protoagent-workflow",
            },
            policy=CapabilityPolicy({"workflow.execute": "allow"}, default_effect="deny"),
            expose_chat=False,
            logger=QUIET_LOGGER,
            verbosity=0,
            run_store=store,
        )
        # The mesh owns its registry and agents. The workflow group owns only its
        # controller and declares the already-running mesh as external resources.
        async with AgentGroup(
            list(deck.values()), registry=registry, own_registry=registry is not None
        ):
            async with AgentGroup([coordinator], external_agents=list(deck.values())) as group:
                workflow = CodingWorkflow(
                    group=group,
                    contract=contract,
                    attempt=attempt,
                    broker=broker,
                    observe=observe,
                    redaction=redaction,
                    prompt=prompt,
                )

                @coordinator.tool(capabilities=["workflow.execute"])
                async def run_workflow() -> str:
                    """Execute the application's bounded coding Graph once."""
                    return await workflow.execute(context)

                if reason := bridge.cancel_reason():
                    context.cancel(reason).attach_to_task(task)
                    task.cancel(reason)
                handle = group.run(coordinator, task, redaction_policy=redaction)
                if run_state is not None:
                    run_state["handle"] = handle
                controls = asyncio.create_task(bridge.serve(handle))
                try:
                    await observe(handle)
                    result = await handle.result()
                finally:
                    controls.cancel()
                    with suppress(asyncio.CancelledError):
                        await controls
                native_report = result.report
                evidence_task = workflow.task or result.task or task
                evidence_report = RunReport.from_task(evidence_task)
                acceptance = await validate_completion(
                    contract, evidence_task, evidence_report, attempt, broker
                )
                status = result.status
                if workflow.uncertain or acceptance.completion["outcome"] == "uncertain":
                    status = "uncertain"
                elif acceptance.completion["outcome"] == "unverified":
                    status = "unverified"
                elif status == "completed" and not acceptance.completion["satisfied"]:
                    status = acceptance.completion["outcome"]
                if result.error and status == "completed":
                    status = "failed"
                answer = _content_to_text(result.output) or workflow.answer
                if status == "unverified":
                    paths = [
                        str(Path(path).relative_to(project))
                        for path in acceptance.completion["applied_paths"]
                    ]
                    answer = (
                        "Changes applied: " + ", ".join(paths) + "."
                        if paths
                        else "No file changes were applied."
                    )
                    answer += "\nStopped as requested; no further commands or repairs were run."
                elif status != "completed":
                    detail = _failure_detail(
                        result.error
                        or (
                            result.task.metadata.get(
                                "cancel_reason" if status == "canceled" else "error"
                            )
                            if result.task
                            else None
                        )
                        or acceptance.completion["message"]
                        or status
                    )
                    if (
                        status == "incomplete"
                        and contract.requires_write
                        and not acceptance.completion["applied"]
                    ):
                        # An unexecuted promise or action printed as text is
                        # not a useful result. Preserve the proposal in the
                        # native trace, and report what actually happened.
                        answer = f"Run incomplete: {acceptance.completion['message']}"
                    elif status == "incomplete" and workflow.continuations:
                        answer = (
                            "Run incomplete: remaining edits were not finished after "
                            f"{workflow.continuations} bounded continuations. "
                            "The current changes have not satisfied the required checks."
                        )
                    else:
                        answer = f"Run {status}: {detail}\n\n{answer}".strip()
                    if acceptance.completion["applied_paths"]:
                        paths = [
                            str(Path(path).relative_to(project))
                            for path in acceptance.completion["applied_paths"]
                        ]
                        answer += (
                            "\n\nChanges already applied: " + ", ".join(paths) + ". "
                            "The task is incomplete; these edits may be partial. "
                            "Inspect /diff and the current files before continuing."
                        )
                if (
                    contract.requires_write
                    or contract.task_kind == "workspace-verification"
                    or acceptance.verification["results"]
                ):
                    answer += "\n\n" + verification_summary(acceptance.verification)
                from .change_review import review_changes

                change_review, applied_diffs = review_changes(attempt, evidence_report)
                if contract.requires_write or change_review["files"]:
                    answer += "\n\n" + change_review["summary"]
                # Native Graph tasks already retain parent and delegated receipts.
                # Preserve the outer handle's normalized terminal task.
                combined = RunReport.from_task(evidence_task)
                report = RunReport.from_events(
                    combined.events,
                    context=context,
                    final_task=native_report.final_task,
                    metadata={
                        "application": "protoagent",
                        "approval_mode": selected_approval_mode,
                        "provider": provider,
                        "model": model,
                        "transport_task_status": result.status,
                        "application_status": status,
                        "run_contract": contract.to_dict(),
                        "completion_validation": acceptance.completion,
                        "verification": acceptance.verification,
                        "change_review": change_review,
                        "workflow_continuations": workflow.continuations,
                        "task_record": record.snapshot(),
                        "context_admission": {
                            name: getattr(agent.llm, "protoagent_context_admission", {})
                            for name, agent in deck.items()
                            if agent.llm is not None
                        },
                    },
                )
                store.save_report(report, run_id=context.run_id, agent_name="architect")
                transport_report = _transport_report(deck, registry_transport)
        previews = _approval_previews(bridge.approval_requests)
        return redaction.redact(
            {
                "provider": provider,
                "model": model,
                "responder": "architect",
                "answer": answer or "(model returned an empty response)",
                "status": status,
                "events": events,
                "run_events": [event.to_dict() for event in report.events],
                "run_report": report.to_dict(),
                "diffs": applied_diffs,
                "proposal_diffs": previews["diffs"],
                "change_review": change_review,
                "targets": previews["targets"],
                "approval_requests": bridge.approval_requests,
                "approval_decisions": bridge.approval_decisions,
                "approval_mode": selected_approval_mode,
                "run_context": (
                    RunContext.from_task(result.task) if result.task else context
                ).to_dict(),
                "prompt_profile": prompt_profile,
                "run_contract": contract.to_dict(),
                "completion_validation": acceptance.completion,
                "verification": acceptance.verification,
                "task_record": record.snapshot(),
                "workflow_continuations": workflow.continuations,
                "context_admission": {
                    name: getattr(agent.llm, "protoagent_context_admission", {})
                    for name, agent in deck.items()
                    if agent.llm is not None
                },
                "transport_report": transport_report,
            }
        )


def _runtime_urls() -> dict[str, str]:
    """Resolve runtime URLs for the Registry and owned agents."""
    host = os.getenv("PROTOAGENT_RUNTIME_HOST", "127.0.0.1")
    return {
        "registry": _env_url("PROTOAGENT_REGISTRY_URL", "REGISTRY_URL") or _local_url(host),
        "architect": _env_url("PROTOAGENT_ARCHITECT_URL", "ARCHITECT_AGENT_URL")
        or _local_url(host),
        "explorer": _env_url("PROTOAGENT_EXPLORER_URL", "EXPLORER_AGENT_URL") or _local_url(host),
        "coder": _env_url("PROTOAGENT_CODER_URL", "CODER_AGENT_URL") or _local_url(host),
        "scout": _env_url("PROTOAGENT_SCOUT_URL", "SCOUT_AGENT_URL") or _local_url(host),
        "tester": _env_url("PROTOAGENT_TESTER_URL", "TESTER_AGENT_URL") or _local_url(host),
        "mcp": _env_url("PROTOAGENT_MCP_URL", "MCP_AGENT_URL") or _local_url(host),
        "verifier": _env_url("PROTOAGENT_VERIFIER_URL", "VERIFIER_AGENT_URL") or _local_url(host),
    }


def _env_url(*names: str) -> str | None:
    """Return the first configured URL from a list of environment names."""
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def _local_url(host: str) -> str:
    """Allocate an in-process identity or a localhost URL for the selected mesh."""
    if _agent_transport() in {"local", "runtime"}:
        import uuid

        return f"runtime://protoagent-{uuid.uuid4().hex}"
    return f"http://{host}:{_free_port(host)}"


def _free_port(host: str) -> int:
    """Find an available port, falling back when port probing is blocked."""
    global _FALLBACK_PORT
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, 0))
            return int(sock.getsockname()[1])
    except OSError:
        _FALLBACK_PORT += 1
        return _FALLBACK_PORT


def _runtime_timeout() -> int:
    """Read the AgentClient timeout from the environment."""
    raw = os.getenv("PROTOAGENT_AGENT_TIMEOUT", "600")
    try:
        return max(1, int(raw))
    except ValueError:
        return 600


def _run_budget(provider: str, model: str, budget_type):
    """Build ProtoLink's typed budget carrier for this application run."""
    return budget_type(
        max_steps=_env_int("PROTOAGENT_RUN_MAX_STEPS") or 80,
        max_llm_calls=_env_int("PROTOAGENT_RUN_MAX_LLM_CALLS"),
        max_tool_calls=_env_int("PROTOAGENT_RUN_MAX_TOOL_CALLS") or 80,
        max_runtime_seconds=_env_float("PROTOAGENT_RUN_MAX_SECONDS") or float(_runtime_timeout()),
        # Native token budgets accumulate across calls; a model's context
        # window only describes one request and must not cap the whole run.
        max_input_tokens=_env_int("PROTOAGENT_RUN_MAX_INPUT_TOKENS"),
        max_output_tokens=_env_int("PROTOAGENT_RUN_MAX_OUTPUT_TOKENS"),
        metadata={
            "provider": provider,
            "model": model,
            "source": "protoagent-runtime",
        },
    )


def _trace_telemetry(redaction=None):
    """Create ProtoLink local telemetry when explicitly requested."""
    if os.getenv("PROTOAGENT_TRACE", "0").strip().lower() not in {"1", "true", "yes", "on"}:
        return None
    try:
        from protolink import LocalTraceTelemetry
    except Exception:
        return None
    from .checkpoints import private_file
    from .config import CONFIG_DIR
    from .runtime_storage import output_redaction

    policy = redaction or output_redaction()
    return LocalTraceTelemetry(
        path=private_file(CONFIG_DIR / "traces.jsonl"), redactor=policy.redact
    )


def _env_int(name: str) -> int | None:
    return _optional_int(os.getenv(name))


def _env_float(name: str) -> float | None:
    try:
        value = float(os.getenv(name, ""))
    except ValueError:
        return None
    return value if value > 0 else None


def _optional_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _agent_transport() -> TransportName:
    """Return the ProtoLink transport used by local agents and the client."""
    transport = os.getenv("PROTOAGENT_AGENT_TRANSPORT", "local").strip().lower()
    aliases = {
        "jsonrpc": "sse",
        "json-rpc": "sse",
        "sse-jsonrpc": "sse",
        "sse-json-rpc": "sse",
    }
    normalized = aliases.get(transport, transport or "local")
    if normalized not in {
        "local",
        "http",
        "websocket",
        "sse",
        "json-rpc",
        "sse-json-rpc",
        "grpc",
        "runtime",
    }:
        return "local"
    return normalized


def _streaming_enabled() -> bool:
    """Control live presentation; ProtoLink selects each peer's transport capability."""
    raw = os.getenv("PROTOAGENT_STREAM", "1").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    return True


def _transport_report(deck: dict[str, Any], registry_transport) -> dict[str, Any]:
    """Return ProtoLink's structured transport configuration and live counters."""
    return {
        "registry": _transport_snapshot(registry_transport),
        "entry": "local RunHandle",
        "delegation": "owned local children" if deck["architect"].subagents else "transport mesh",
        "agents": {
            name: _transport_snapshot(getattr(agent, "transport", None))
            for name, agent in deck.items()
        },
    }


def _transport_snapshot(transport) -> dict[str, Any]:
    """Serialize one first-party ProtoLink transport diagnostics snapshot."""
    if transport is None:
        return {}
    capabilities = transport.capabilities
    config = transport.config
    return {
        "transport": str(getattr(transport, "transport_type", "unknown")),
        "url": str(getattr(transport, "url", "")),
        "capabilities": {
            "networked": bool(capabilities.networked),
            "streaming": bool(capabilities.streaming),
            "tls": bool(capabilities.tls),
            "bidirectional": bool(capabilities.bidirectional),
            "persistent_connections": bool(capabilities.persistent_connections),
        },
        "config": config.to_dict(),
        "metrics": transport.metrics.to_dict(),
    }


def _append_event(
    events: list[str],
    message: str,
    bridge: RuntimeBridge | None,
    *,
    run_event: dict[str, Any] | None = None,
) -> None:
    """Append a trace event without letting token streams flood the CLI."""
    raw_limit = os.getenv("PROTOAGENT_STREAM_TRACE_LIMIT", "120")
    try:
        limit = max(20, int(raw_limit))
    except ValueError:
        limit = 120
    if len(events) < limit:
        events.append(message)
        if bridge is not None:
            bridge.emit(message, run_event=run_event)
    else:
        if not events[-1].startswith("Live trace preview limit"):
            limit_message = (
                f"Live trace preview limit ({limit}) reached. The run continues; "
                "full events remain available in /trace."
            )
            events.append(limit_message)
            if bridge is not None:
                bridge.emit(limit_message)
        # Keep decisions and outcomes visible after suppressing repetitive model
        # summaries. The complete native report is stored independently.
        event_type = str((run_event or {}).get("type", ""))
        if bridge is not None and (
            event_type.startswith(("approval.", "user_input."))
            or (event_type == "task.status" and bool((run_event or {}).get("final")))
            or event_type
            in {
                "action.completed",
                "action.started",
                "llm.call.started",
                "action.failed",
                "action.denied",
                "workflow.blocked",
                "task.blocked",
                "task.error",
                "budget.exceeded",
                "tool.correction.exhausted",
                "verification.deferred",
                "worker.handoff",
                "worker.continuing",
                "workflow.continuing",
                "workflow.repair.requested",
                "workflow.work_yielded",
                "run.completed",
                "run.failed",
                "run.canceled",
            }
        ):
            bridge.emit(message, run_event=run_event)


def _run_event_summary(event: dict[str, Any]) -> str:
    """Keep model/process text in the live-output channel rather than trace summaries."""
    raw_payload = event.get("payload")
    payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
    if payload.get("llm_event_type") == "llm_chunk" or event.get("type") == "process.output":
        return ""
    return str(event.get("summary") or event.get("type") or "runtime event")


def _content_to_text(content: Any) -> str:
    """Extract readable text from a ProtoLink response payload."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, dict):
        if "content" in content:
            return _content_to_text(content["content"])
        if "result" in content:
            return _content_to_text(content["result"])
        if "text" in content:
            return _content_to_text(content["text"])
    if isinstance(content, list):
        return "\n".join(filter(None, (_content_to_text(item) for item in content))).strip()
    return str(content).strip()


def _failure_detail(error: Any) -> str:
    """Project nested native error envelopes into prose; retain originals in /trace."""
    import ast
    import re

    for _ in range(6):
        if isinstance(error, dict) and "message" in error:
            error = error["message"]
            continue
        if isinstance(error, str) and error.startswith("Native run failed: "):
            candidate = error.removeprefix("Native run failed: ")
            if len(candidate) <= 10_000:
                try:
                    error = ast.literal_eval(candidate)
                    continue
                except (SyntaxError, ValueError, RecursionError):
                    pass
        break
    detail = str(error)
    if "prediction aborted, token repeat limit reached" in detail:
        return (
            "Ollama stopped the model because its generated tokens began repeating. "
            "The interrupted response was not executed as a tool request. "
            "This is a generation failure, separate from the agent's turn limit."
        )
    limit = re.search(r"Maximum inference steps \((\d+)\) exceeded", detail)
    if limit:
        worker = re.search(r"Tool '([^']+)' execution failed:", detail)
        name = worker.group(1).capitalize() if worker else "Agent"
        return f"{name} reached its {limit.group(1)}-turn limit before finishing."
    return detail


def _approval_previews(requests: list[dict[str, Any]]) -> dict[str, list[Any]]:
    """Extract displayable previews from typed approval request artifacts."""
    targets: list[str] = []
    diffs: list[dict[str, str]] = []
    for request in requests:
        raw_action = request.get("action")
        action: dict[str, Any] = raw_action if isinstance(raw_action, dict) else {}
        raw_metadata = action.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        raw_payload = action.get("payload")
        payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
        raw_arguments = payload.get("arguments")
        arguments: dict[str, Any] = raw_arguments if isinstance(raw_arguments, dict) else {}
        recovery = payload.get("recovery", {})
        before = recovery.get("before", recovery.get("change", {}).get("before", {}))
        path = str(
            metadata.get("path")
            or arguments.get("path")
            or before.get("revision", {}).get("resource_id")
            or ""
        )
        if path:
            targets.append(path)
        for artifact in action.get("artifacts") or []:
            if not isinstance(artifact, dict) or not (
                artifact.get("media_type") == "text/x-diff"
                or (
                    artifact.get("kind") == "preview"
                    and artifact.get("metadata", {}).get("preimage")
                )
            ):
                continue
            for part in artifact.get("parts") or []:
                if not isinstance(part, dict) or not isinstance(part.get("content"), str):
                    continue
                diff = part["content"]
                if diff.strip():
                    diffs.append({"path": path, "diff": diff, "source": "coder"})
    return {"targets": sorted(set(targets)), "diffs": _dedupe_diffs(diffs)}


def _dedupe_diffs(diffs: list[dict[str, str]]) -> list[dict[str, str]]:
    """Remove duplicate diff payloads while preserving order."""
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, str]] = []
    for item in diffs:
        key = (item.get("path", ""), item.get("diff", ""))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def run_recovery(workspace: str, change_id: str, session_id=None, progress_path=None):
    """Run an exact approved native restoration without constructing a model."""
    from protolink import AgentGroup, ApprovalBroker, RunContext, Task
    from protolink.storage import SQLiteRunStore, SQLiteStorage

    from . import config
    from .agents.coder import create_coder_agent
    from .checkpoints import checkpoint_store, private_file, select_change, workspace_writer
    from .runtime_policy import RunAuthorization
    from .runtime_storage import output_redaction

    bridge = RuntimeBridge(progress_path)
    submitted = False

    async def restore():
        nonlocal submitted
        context = RunContext(
            session_id=session_id, workspace_uri=Path(workspace).resolve().as_uri()
        )
        context.trace_id = context.run_id
        authorization = RunAuthorization(context)
        redaction = output_redaction()
        with workspace_writer(workspace):
            checkpoints = checkpoint_store(workspace)
            selected = select_change(checkpoints, change_id, workspace)
            store = SQLiteRunStore(
                private_file(config.CONFIG_DIR / "runs" / f"{context.run_id}.sqlite"),
                redaction_policy=redaction,
            )
            broker = ApprovalBroker(
                storage=SQLiteStorage(
                    store.db_path, table_name="approvals", namespace=context.run_id
                ),
                timeout_seconds=float(_runtime_timeout()),
            )
            bridge.bind(broker, authorization, redaction)
            agent = create_coder_agent(
                workspace=workspace,
                transport=None,
                tool_only=True,
                checkpoints=checkpoints,
                approval_handler=broker,
                authorization=authorization,
            )
            task = Task.create_tool_call(
                tool_name="restore_change", args={"change_id": selected.change_id}
            )
            if reason := bridge.cancel_reason():
                context.cancel(reason).attach_to_task(task)
                task.cancel(reason)
            else:
                context.attach_to_task(task)
            async with AgentGroup([agent]) as group:
                handle = group.run(agent, task, store=store, redaction_policy=redaction)
                submitted = True
                controls = asyncio.create_task(bridge.serve(handle))
                try:
                    async for event in handle.events():
                        bridge.emit(_run_event_summary(event.to_dict()), run_event=event.to_dict())
                    result = await handle.result()
                finally:
                    controls.cancel()
                    with suppress(asyncio.CancelledError):
                        await controls
            record = checkpoints.get(selected.change_id)
            restored = (
                result.status == "completed" and record is not None and record.state == "restored"
            )
            uncertain = record is not None and record.state in {"restoring", "uncertain"}
            status = (
                "answered"
                if restored
                else "uncertain"
                if uncertain
                else result.status
                if result.status != "completed"
                else "blocked"
            )
            return redaction.redact(
                {
                    "status": status,
                    "answer": f"Restored {selected.before.revision.resource_id} from {selected.change_id}."
                    if restored
                    else f"Undo {status}: {result.error or (result.task.metadata.get('error') if result.task else None) or 'inspect the recovery record before requesting new work'}",
                    "file_target": selected.before.revision.resource_id,
                    "run_context": (
                        RunContext.from_task(result.task) if result.task else context
                    ).to_dict(),
                    "approval_requests": bridge.approval_requests,
                    "approval_decisions": bridge.approval_decisions,
                    "run_report": result.report.to_dict(),
                    "run_events": [event.to_dict() for event in result.report.events],
                }
            )

    try:
        return asyncio.run(restore())
    except Exception as exc:
        return {
            "status": "uncertain" if submitted else "blocked",
            "answer": f"Undo interrupted after submission: {exc}. Inspect the resource and checkpoint before requesting new work."
            if submitted
            else f"Undo was not submitted: {exc}",
            "file_target": "",
        }
    finally:
        bridge.cleanup()
