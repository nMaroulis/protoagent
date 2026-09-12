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
TransportName = Literal["http", "websocket", "sse", "json-rpc", "sse-json-rpc", "grpc", "runtime"]


def run_selected_model(
    prompt: str,
    workspace: str | None = None,
    session_id: str | None = None,
    progress_path: str | None = None,
    user_prompt: str | None = None,
) -> dict[str, Any]:
    """Run the selected model through the ProtoLink Architect agent.

    AgentGroup owns the embedded lifecycle. A native RunHandle executes a
    bounded application Graph; Architect delegates through the configured
    ProtoLink transports and Registry.
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
    run_state: dict[str, Any] | None = None,
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
    from protolink.storage import SQLiteStorage

    from . import config
    from .checkpoints import checkpoint_store, workspace_writer
    from .runtime_policy import AttemptState, RunAuthorization
    from .runtime_storage import ApplicationRunStore, output_redaction
    from .workflow import CodingWorkflow

    project = str(Path(workspace or os.getenv("PROTOAGENT_WORKSPACE", os.getcwd())).resolve())
    contract = infer_run_contract(user_prompt or prompt)
    context = RunContext(
        session_id=session_id,
        workspace_uri=Path(project).as_uri(),
        permissions={scope: "allow" for scope in RUNTIME_SCOPES},
        budget=_run_budget(provider, model, RunBudget),
        metadata={
            "application": "protoagent",
            "interface": "rust-cli",
            "prompt_profile": prompt_profile,
            "run_contract": contract.to_dict(),
        },
    )
    context.trace_id = context.run_id
    authorization = RunAuthorization(context)
    auth = create_runtime_auth()
    redaction = output_redaction(auth.credentials)
    bridge.redaction = redaction
    events: list[str] = []
    live_updates = _streaming_enabled(_agent_transport())

    def emit(message):
        events.append(message)
        bridge.emit(message)

    async def observe(handle):
        async for event in handle.events():
            data = event.to_dict(redaction_policy=redaction)
            summary = _run_event_summary(data)
            if data["type"] == "process.output":
                summary = f"Verifier {data['payload'].get('channel', 'output')}: {data['payload'].get('text', '')}"
            if summary:
                _append_event(events, summary, bridge if live_updates else None, run_event=data)

    with workspace_writer(project):
        store = ApplicationRunStore(
            config.CONFIG_DIR / "runs" / f"{context.run_id}.sqlite", redaction
        )
        broker = ApprovalBroker(
            storage=SQLiteStorage(store.db_path, table_name="approvals", namespace=context.run_id),
            timeout_seconds=float(_runtime_timeout()),
        )
        bridge.bind(broker, authorization, redaction)
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
        attempt = AttemptState(project, checkpoints, authorization)
        urls = _runtime_urls()
        transport = _agent_transport()
        registry_transport = create_configured_transport(
            "runtime" if transport == "runtime" else "http",
            urls["registry"],
            timeout=_runtime_timeout(),
        )
        assert registry_transport is not None
        registry = Registry(transport=registry_transport, verbosity=0)
        deck = create_agent_deck(
            registry=registry,
            provider=provider,
            model=model,
            workspace=project,
            urls=urls,
            transport=transport,
            approval_handler=broker,
            telemetry=_trace_telemetry(redaction),
            prompt_profile=str(prompt_profile["resolved"]),
            scout_enabled=scout_enabled,
            auth=auth,
            checkpoints=checkpoints,
            authorization=authorization,
            attempt=attempt,
        )
        for agent in deck.values():
            agent.run_store = store
        compaction_reports = await compact_agent_histories_for_run(deck.values(), session_id)
        for report in compaction_reports:
            if report.get("changed"):
                emit(f"Compacted {report['agent']} conversation history.")
        emit(f"Runtime: ProtoLink AgentGroup; {transport} worker transport; {provider} / {model}.")
        emit(f"Project: {project}. Run: {context.run_id}. Repair limit: 2.")
        coordinator = Agent(
            card={
                "name": "protoagent-workflow",
                "description": "Application coding workflow",
                "url": "runtime://protoagent-workflow",
            },
            policy=CapabilityPolicy({"workflow.execute": "allow"}, default_effect="deny"),
            expose_chat=False,
            verbosity=0,
            run_store=store,
        )
        # The mesh owns its registry and agents. The workflow group owns only its
        # controller and declares the already-running mesh as external resources.
        async with AgentGroup(list(deck.values()), registry=registry, own_registry=True):
            async with AgentGroup([coordinator], external_agents=list(deck.values())) as group:
                workflow = CodingWorkflow(
                    group=group,
                    contract=contract,
                    attempt=attempt,
                    broker=broker,
                    store=store,
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
                evidence_report = store.trace_report(evidence_task, observed=native_report.events)
                acceptance = await validate_completion(
                    contract, evidence_task, evidence_report, attempt, broker
                )
                status = result.status
                if workflow.uncertain or acceptance.completion["outcome"] == "uncertain":
                    status = "uncertain"
                elif status == "completed" and not acceptance.completion["satisfied"]:
                    status = acceptance.completion["outcome"]
                if result.error and status == "completed":
                    status = "failed"
                answer = _content_to_text(result.output) or workflow.answer
                if status != "completed":
                    detail = str(
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
                    answer = f"Run {status}: {detail}\n\n{answer}".strip()
                if (
                    contract.requires_write
                    or contract.task_kind == "workspace-verification"
                    or acceptance.verification["results"]
                ):
                    answer += "\n\n" + verification_summary(acceptance.verification)
                # Keep the handle's normalized terminal task; combine native child
                # receipts for application inspection, without parsing wire events.
                combined = store.trace_report(evidence_task, observed=native_report.events)
                report = RunReport.from_events(
                    combined.events,
                    context=context,
                    final_task=native_report.final_task,
                    metadata={
                        "application": "protoagent",
                        "provider": provider,
                        "model": model,
                        "transport_task_status": result.status,
                        "application_status": status,
                        "run_contract": contract.to_dict(),
                        "completion_validation": acceptance.completion,
                        "verification": acceptance.verification,
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
                "diffs": previews["diffs"],
                "targets": previews["targets"],
                "approval_requests": bridge.approval_requests,
                "approval_decisions": bridge.approval_decisions,
                "run_context": (
                    RunContext.from_task(result.task) if result.task else context
                ).to_dict(),
                "prompt_profile": prompt_profile,
                "run_contract": contract.to_dict(),
                "completion_validation": acceptance.completion,
                "verification": acceptance.verification,
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
    if _agent_transport() == "runtime":
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
    transport = os.getenv("PROTOAGENT_AGENT_TRANSPORT", "sse").strip().lower()
    aliases = {
        "jsonrpc": "sse",
        "json-rpc": "sse",
        "sse-jsonrpc": "sse",
        "sse-json-rpc": "sse",
    }
    normalized = aliases.get(transport, transport or "sse")
    if normalized not in {
        "http",
        "websocket",
        "sse",
        "json-rpc",
        "sse-json-rpc",
        "grpc",
        "runtime",
    }:
        return "sse"
    return normalized


def _streaming_enabled(transport: TransportName) -> bool:
    """Decide whether to consume ProtoLink task streams for this run."""
    raw = os.getenv("PROTOAGENT_STREAM", "1").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return transport != "http"
    return transport != "http"


def _transport_report(deck: dict[str, Any], registry_transport) -> dict[str, Any]:
    """Return ProtoLink's structured transport configuration and live counters."""
    return {
        "registry": _transport_snapshot(registry_transport),
        "entry": "local RunHandle",
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
    elif not events[-1].startswith("Stream trace limit reached"):
        limit_message = (
            f"Stream trace limit reached ({limit}); suppressing further event summaries."
        )
        events.append(limit_message)
        if bridge is not None:
            bridge.emit(limit_message)


def _run_event_summary(event: dict[str, Any]) -> str:
    """Return the stable RunEvent summary while suppressing token chunks."""
    raw_payload = event.get("payload")
    payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
    if payload.get("llm_event_type") == "llm_chunk":
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
    from protolink.storage import SQLiteStorage

    from . import config
    from .agents.coder import create_coder_agent
    from .checkpoints import checkpoint_store, select_change, workspace_writer
    from .runtime_policy import RunAuthorization
    from .runtime_storage import ApplicationRunStore, output_redaction

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
            store = ApplicationRunStore(
                config.CONFIG_DIR / "runs" / f"{context.run_id}.sqlite", redaction
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
