"""ProtoLink runtime mesh for the CLI core."""

from __future__ import annotations

import asyncio
import os
import socket
from contextlib import suppress
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Literal, cast

from .agents import create_agent_deck
from .agents.common import AgentRuntimeAuth, create_runtime_auth
from .config import load_config, normalize_provider, provider_config
from .history import compact_agent_histories_for_run
from .llm import ollama_context_window
from .prompt_profiles import prompt_profile_status
from .run_contracts import infer_run_contract, validate_run_completion
from .runtime_bridge import RuntimeBridge

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

    The CLI enters the core by sending a Task to Architect through ProtoLink's
    AgentClient. Architect owns orchestration and resolves Explorer/Coder by
    querying the Registry, just like the upstream coding-agent example.
    """
    config = load_config()
    provider = normalize_provider(config.get("active_provider", "ollama"))
    cfg = provider_config(provider, config)
    model = cfg.get("model", "")
    if not model:
        raise RuntimeError(f"No model selected for provider '{provider}'")
    profile = prompt_profile_status(config, provider=provider, model=str(model))

    bridge = RuntimeBridge(progress_path)
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
            )
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
) -> dict[str, Any]:
    """Start the local ProtoLink mesh and send the prompt to Architect."""
    from protolink import DEFAULT_REDACTION_POLICY, RunBudget, RunContext, RunRecorder, Task
    from protolink.client import AgentClient
    from protolink.discovery import Registry

    urls = _runtime_urls()
    agent_transport = _agent_transport()
    streaming = _streaming_enabled(agent_transport)
    events: list[str] = []
    recorder = RunRecorder()
    project = str(Path(workspace or os.getenv("PROTOAGENT_WORKSPACE", os.getcwd())).resolve())
    contract = infer_run_contract(user_prompt or prompt)
    task = Task.create_infer(prompt=prompt)
    context = RunContext(
        session_id=session_id,
        workspace_uri=Path(project).as_uri(),
        permissions={
            "agent.delegate": "allow",
            "workspace.read": "allow",
            "workspace.write": "allow",
        },
        budget=_run_budget(provider, model, RunBudget),
        metadata={
            "application": "protoagent",
            "interface": "rust-cli",
            "prompt_profile": prompt_profile,
            "run_contract": contract.to_dict(),
        },
    )
    context.trace_id = context.run_id
    context.attach_to_task(task)
    recorder.context = context

    def emit(message: str) -> None:
        events.append(message)
        bridge.emit(message)

    def canceled_before_execution() -> dict[str, Any] | None:
        return _preflight_cancellation_result(
            bridge=bridge,
            context=context,
            task=task,
            recorder=recorder,
            events=events,
            emit=emit,
            provider=provider,
            model=model,
        )

    emit(f"Registry prepared at {urls['registry']}.")
    emit(f"All LLM-capable agents configured with {provider} / {model}.")
    emit(
        "Agent prompt profile: "
        f"{prompt_profile['label']} "
        f"(configured {prompt_profile['configured']}, resolved {prompt_profile['resolved']})."
    )
    emit(
        f"Agent transport: {agent_transport} ({'streaming enabled' if streaming else 'request/response mode'})."
    )
    emit(f"Active project workspace: {project}.")
    emit(f"Conversation session: {session_id or 'task-local'}.")
    emit(f"Run contract: {contract.task_kind}; {contract.completion_rule}")
    if provider == "ollama":
        emit(f"Ollama context window: {ollama_context_window()} tokens.")
    emit(f"Run context: {context.run_id}.")
    auth = create_runtime_auth()
    emit("Agent auth: ProtoLink API-key auth enabled for the local mesh.")
    registry = None
    client = None
    deck: dict[str, Any] = {}
    started_agents: list[Any] = []
    cancellation_monitor: asyncio.Task[None] | None = None

    try:
        if canceled := canceled_before_execution():
            return canceled
        registry = Registry(url=urls["registry"], transport="http", verbosity=0)
        registry.start(background=True)
        emit("Registry started.")
        if canceled := canceled_before_execution():
            return canceled

        telemetry = _trace_telemetry()
        deck = create_agent_deck(
            registry=registry,
            provider=provider,
            model=model,
            workspace=workspace,
            urls={
                "explorer": urls["explorer"],
                "coder": urls["coder"],
                "architect": urls["architect"],
            },
            transport=agent_transport,
            approval_handler=bridge.approval_handler,
            telemetry=telemetry,
            prompt_profile=str(prompt_profile["resolved"]),
            auth=auth,
        )
        compaction_reports = await compact_agent_histories_for_run(deck.values(), session_id)
        for report in compaction_reports:
            if report.get("changed"):
                emit(
                    "ProtoLink compacted "
                    f"{str(report['agent']).title()} history: removed "
                    f"{report['removed_messages']} message(s); "
                    f"{report['after_tokens']} estimated token(s) remain."
                )
        if canceled := canceled_before_execution():
            return canceled

        for name in ("explorer", "coder", "architect"):
            deck[name].start(background=True)
            started_agents.append(deck[name])
            emit(f"{name.title()} registered at {deck[name].card.url}.")
            if canceled := canceled_before_execution():
                return canceled

        await asyncio.sleep(float(os.getenv("PROTOAGENT_DISCOVERY_DELAY", "0.15")))
        discovered = await deck["architect"].discover_agents()
        names = ", ".join(sorted(card.name for card in discovered)) or "none"
        emit(f"Architect discovery sees: {names}.")
        if canceled := canceled_before_execution():
            return canceled

        client = AgentClient(
            transport=_authenticated_client_transport(
                agent_transport,
                urls["client"],
                auth,
                timeout=_runtime_timeout(),
            )
        )
        cancellation_monitor = asyncio.create_task(
            _monitor_cancellation(
                bridge=bridge,
                client=client,
                agent=deck["architect"],
                agent_url=deck["architect"].card.url,
                task_id=task.id,
                emit=emit,
            )
        )
        if streaming:
            emit("AgentClient opened a streaming task channel to Architect.")
            emit("Architect is processing the user task stream.")
            try:
                delivery = await _send_task_streaming(
                    client=client,
                    agent_url=deck["architect"].card.url,
                    task=task,
                    context=context,
                    recorder=recorder,
                    events=events,
                    bridge=bridge,
                )
            except NotImplementedError as exc:
                emit(f"Streaming unavailable for {agent_transport}: {exc}")
                emit("Falling back to request/response task execution.")
                delivery = await _send_task_once(client, deck["architect"].card.url, task)
        else:
            emit("AgentClient sent the user task to Architect.")
            emit("Architect is processing the request/response task.")
            delivery = await _send_task_once(client, deck["architect"].card.url, task)

        status = str(delivery.get("status") or "completed")
        raw_answer = delivery.get("content")
        final_context = _context_from_delivery(delivery) or context
        run_report = _run_report_to_dict(
            recorder,
            context=final_context,
            final_task=delivery.get("task"),
            provider=provider,
            model=model,
            redaction_policy=DEFAULT_REDACTION_POLICY,
        )
        emit("Architect returned a final task response.")

        answer = _content_to_text(raw_answer)
        if not answer:
            answer = (
                f"Task canceled: {final_context.cancel_reason or bridge.cancel_reason() or 'canceled by user'}"
                if status == "canceled"
                else "(model returned an empty response)"
            )

        previews = _approval_previews(bridge.approval_requests)
        events.extend(
            _approval_event_summaries(bridge.approval_requests, bridge.approval_decisions)
        )
        run_events = _run_events_to_list(recorder, redaction_policy=DEFAULT_REDACTION_POLICY)
        completion = validate_run_completion(
            contract,
            answer=answer,
            status=status,
            run_events=run_events,
            approval_requests=bridge.approval_requests,
            diff_items=previews["diffs"],
        )
        if completion.outcome == "incomplete":
            missing = " ".join(completion.missing)
            emit(f"Run contract incomplete: {missing}")
            answer = (
                "Runtime completion guard: the model ended before satisfying the "
                f"required worker/artifact contract. {missing}\n\n{answer}"
            )
            status = "incomplete"
        elif completion.outcome == "blocked":
            emit("Run contract blocked: model reported an explicit blocker before writing.")
            status = "blocked"
        elif completion.outcome == "satisfied":
            emit("Run contract satisfied by Coder delegation or write artifact.")

        return {
            "provider": provider,
            "model": model,
            "responder": "architect",
            "answer": answer,
            "status": status,
            "events": events,
            "run_events": run_events,
            "run_report": run_report,
            "diffs": previews["diffs"],
            "targets": previews["targets"],
            "approval_requests": bridge.approval_requests,
            "approval_decisions": bridge.approval_decisions,
            "run_context": final_context.to_dict(),
            "prompt_profile": prompt_profile,
            "run_contract": contract.to_dict(),
            "completion_validation": completion.to_dict(),
        }
    finally:
        if cancellation_monitor is not None:
            cancellation_monitor.cancel()
            with suppress(asyncio.CancelledError):
                await cancellation_monitor
        if client is not None:
            transport = getattr(client, "_transport", None)
            if transport is not None and hasattr(transport, "stop"):
                with suppress(Exception):
                    await transport.stop()
        for agent in reversed(started_agents):
            with suppress(Exception):
                agent.stop()
        if registry is not None:
            with suppress(Exception):
                registry.stop()


def _runtime_urls() -> dict[str, str]:
    """Resolve runtime URLs for the Registry, client, and agents."""
    host = os.getenv("PROTOAGENT_RUNTIME_HOST", "127.0.0.1")
    return {
        "registry": _env_url("PROTOAGENT_REGISTRY_URL", "REGISTRY_URL") or _local_url(host),
        "client": _env_url("PROTOAGENT_CLIENT_URL", "CLIENT_URL") or _local_url(host),
        "architect": _env_url("PROTOAGENT_ARCHITECT_URL", "ARCHITECT_AGENT_URL")
        or _local_url(host),
        "explorer": _env_url("PROTOAGENT_EXPLORER_URL", "EXPLORER_AGENT_URL") or _local_url(host),
        "coder": _env_url("PROTOAGENT_CODER_URL", "CODER_AGENT_URL") or _local_url(host),
    }


def _env_url(*names: str) -> str | None:
    """Return the first configured URL from a list of environment names."""
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def _local_url(host: str) -> str:
    """Build a localhost URL with an available port."""
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
    cfg = provider_config(provider)
    context_window = (
        ollama_context_window(cfg)
        if provider == "ollama"
        else _env_or_config_int(
            "PROTOAGENT_RUN_MAX_INPUT_TOKENS",
            cfg.get("context_window"),
        )
    )
    return budget_type(
        max_steps=_env_int("PROTOAGENT_RUN_MAX_STEPS"),
        max_llm_calls=_env_int("PROTOAGENT_RUN_MAX_LLM_CALLS"),
        max_tool_calls=_env_int("PROTOAGENT_RUN_MAX_TOOL_CALLS"),
        max_runtime_seconds=_env_float("PROTOAGENT_RUN_MAX_SECONDS") or float(_runtime_timeout()),
        max_input_tokens=context_window,
        max_output_tokens=_env_int("PROTOAGENT_RUN_MAX_OUTPUT_TOKENS"),
        metadata={
            "provider": provider,
            "model": model,
            "source": "protoagent-runtime",
        },
    )


def _trace_telemetry():
    """Create ProtoLink local telemetry when explicitly requested."""
    if os.getenv("PROTOAGENT_TRACE", "0").strip().lower() not in {"1", "true", "yes", "on"}:
        return None
    try:
        from protolink import LocalTraceTelemetry
    except Exception:
        return None
    raw_dir = os.getenv("PROTOAGENT_CONFIG_DIR")
    config_dir = Path(raw_dir).expanduser() if raw_dir else Path.home() / ".protoagent"
    config_dir.mkdir(parents=True, exist_ok=True)
    return LocalTraceTelemetry(path=config_dir / "traces.jsonl")


def _env_int(name: str) -> int | None:
    return _optional_int(os.getenv(name))


def _env_float(name: str) -> float | None:
    try:
        value = float(os.getenv(name, ""))
    except ValueError:
        return None
    return value if value > 0 else None


def _env_or_config_int(name: str, fallback: Any) -> int | None:
    return _env_int(name) or _optional_int(fallback)


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
    return cast(TransportName, normalized)


def _streaming_enabled(transport: TransportName) -> bool:
    """Decide whether to consume ProtoLink task streams for this run."""
    raw = os.getenv("PROTOAGENT_STREAM", "1").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return transport != "http"
    return transport != "http"


def _authenticated_client_transport(
    transport: TransportName,
    url: str,
    auth: AgentRuntimeAuth,
    *,
    timeout: int,
):
    """Create a ProtoLink client transport with the deck's runtime auth."""
    from protolink.transport import get_transport

    return get_transport(
        transport=transport,
        url=url,
        timeout=timeout,
        authenticator=auth.authenticator,
        credentials=auth.credentials,
    )


async def _send_task_once(client, agent_url: str, task) -> Any:
    """Send a task through ProtoLink's request/response client path."""
    result_task = await client.send_task(agent_url=agent_url, task=task)
    return {
        "content": _normalize(result_task.get_last_part_content()),
        "status": getattr(result_task.state, "value", result_task.state),
        "task": _normalize(result_task),
    }


async def _send_task_streaming(
    *,
    client,
    agent_url: str,
    task,
    context,
    recorder,
    events: list[str],
    bridge: RuntimeBridge,
) -> Any:
    """Consume ProtoLink streaming events and return the final answer payload."""
    from protolink import DEFAULT_REDACTION_POLICY

    final_task: dict[str, Any] | None = None
    final_content: Any = None
    artifact_content: Any = None
    final_status = "completed"

    async for event in client.send_task_streaming(agent_url=agent_url, task=task):
        payload = _normalize(event)
        if not isinstance(payload, dict):
            _append_event(events, f"Stream event: {_content_to_text(payload)}", bridge)
            continue

        if _is_llm_chunk_payload(payload):
            continue

        run_event = await recorder.record_task_event(event, context=context)
        run_event_data = run_event.to_dict(redaction_policy=DEFAULT_REDACTION_POLICY)
        summary = _run_event_summary(run_event_data)
        if summary:
            _append_event(events, summary, bridge, run_event=run_event_data)

        event_type = payload.get("type")
        if event_type == "task_error":
            raise RuntimeError(payload.get("error_message") or "Agent stream returned an error")

        if event_type == "task_status_update":
            final_status = str(payload.get("new_state") or final_status)
            metadata = payload.get("metadata", {})
            if (
                payload.get("final")
                and isinstance(metadata, dict)
                and isinstance(metadata.get("task"), dict)
            ):
                final_task = metadata["task"]
            continue

        if event_type == "task_artifact_update":
            artifact = payload.get("artifact")
            artifact_content = _item_last_part_content(artifact)
            continue

        if event_type == "task_llm_stream":
            content = payload.get("content")
            if payload.get("llm_event_type") == "llm_final" or payload.get("final"):
                final_content = content

    content = final_content
    if final_task:
        final_status = str(final_task.get("state") or final_status)
        if final_status == "canceled":
            content = None
        else:
            task_content = _task_last_part_content(final_task)
            if task_content is not None:
                content = task_content
    elif artifact_content is not None:
        content = artifact_content
    return {"content": _normalize(content), "status": final_status, "task": final_task}


def _normalize(value: Any) -> Any:
    """Convert dataclasses and ProtoLink objects into plain containers."""
    if hasattr(value, "to_dict"):
        return _normalize(value.to_dict())
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


def _run_events_to_list(recorder, *, redaction_policy=None) -> list[dict[str, Any]]:
    """Serialize recorded RunEvents for the Rust UI."""
    return [event.to_dict(redaction_policy=redaction_policy) for event in recorder.events]


def _run_report_to_dict(
    recorder,
    *,
    context,
    final_task: dict[str, Any] | None,
    provider: str,
    model: str,
    redaction_policy=None,
) -> dict[str, Any]:
    """Build ProtoLink's durable application-facing run report."""
    report = recorder.to_report(
        context=context,
        final_task=final_task,
        metadata={
            "application": "protoagent",
            "interface": "rust-cli",
            "provider": provider,
            "model": model,
        },
    )
    return report.to_dict(redaction_policy=redaction_policy)


def _is_llm_chunk_payload(payload: dict[str, Any]) -> bool:
    """Return true for raw token chunks that should not become UI trace rows."""
    return payload.get("type") == "task_llm_stream" and payload.get("llm_event_type") == "llm_chunk"


def _append_event(
    events: list[str],
    message: str,
    bridge: RuntimeBridge,
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
        bridge.emit(message, run_event=run_event)
    elif not events[-1].startswith("Stream trace limit reached"):
        limit_message = (
            f"Stream trace limit reached ({limit}); suppressing further event summaries."
        )
        events.append(limit_message)
        bridge.emit(limit_message)


def _run_event_summary(event: dict[str, Any]) -> str:
    """Return the stable RunEvent summary while suppressing token chunks."""
    raw_payload = event.get("payload")
    payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
    if payload.get("llm_event_type") == "llm_chunk":
        return ""
    return str(event.get("summary") or event.get("type") or "runtime event")


def _context_from_delivery(delivery: dict[str, Any]):
    """Read the final serialized RunContext returned by Protolink."""
    from protolink import RunContext

    task = delivery.get("task")
    if not isinstance(task, dict):
        return None
    metadata = task.get("metadata")
    if not isinstance(metadata, dict) or not isinstance(metadata.get("run_context"), dict):
        return None
    return RunContext.from_dict(metadata["run_context"])


async def _monitor_cancellation(
    *,
    bridge: RuntimeBridge,
    client,
    agent=None,
    agent_url: str,
    task_id: str,
    emit,
) -> None:
    """Forward a Rust cancellation signal through Protolink's control plane."""
    from protolink import TaskCancellationRequest, TaskNotCancelableError, TaskNotFoundError

    while True:
        reason = bridge.cancel_reason()
        if not reason:
            await asyncio.sleep(0.08)
            continue
        request = TaskCancellationRequest(
            id=task_id,
            reason=reason,
            metadata={"requested_by": "protoagent-tui"},
        )
        accepted = False
        if agent is not None:
            try:
                await agent.cancel_task(request)
                accepted = True
            except TaskNotCancelableError:
                emit(f"Cancellation arrived after task {task_id} reached a terminal state.")
                return
            except TaskNotFoundError:
                pass
            except Exception:
                pass
        try:
            if not accepted:
                await client.cancel_task(
                    agent_url=agent_url,
                    task_id=task_id,
                    reason=reason,
                    metadata=request.metadata,
                )
                accepted = True
        except TaskNotCancelableError:
            emit(f"Cancellation arrived after task {task_id} reached a terminal state.")
            return
        except Exception:
            await asyncio.sleep(0.08)
            continue
        if accepted:
            emit(f"Cancellation accepted for task {task_id}: {reason}.")
            return


def _preflight_cancellation_result(
    *,
    bridge: RuntimeBridge,
    context,
    task,
    recorder,
    events: list[str],
    emit,
    provider: str,
    model: str,
) -> dict[str, Any] | None:
    """Return a canceled result when the application canceled before task submission."""
    from protolink import DEFAULT_REDACTION_POLICY

    reason = bridge.cancel_reason()
    if not reason:
        return None
    canceled_context = context.cancel(reason)
    canceled_context.attach_to_task(task)
    task.cancel(reason)
    emit(f"Task canceled before model execution: {reason}.")
    return {
        "provider": provider,
        "model": model,
        "responder": "architect",
        "answer": f"Task canceled: {reason}",
        "status": "canceled",
        "events": events,
        "run_events": _run_events_to_list(recorder, redaction_policy=DEFAULT_REDACTION_POLICY),
        "run_report": _run_report_to_dict(
            recorder,
            context=canceled_context,
            final_task=task.to_dict(),
            provider=provider,
            model=model,
            redaction_policy=DEFAULT_REDACTION_POLICY,
        ),
        "diffs": [],
        "targets": [],
        "approval_requests": bridge.approval_requests,
        "approval_decisions": bridge.approval_decisions,
        "run_context": canceled_context.to_dict(),
    }


def _task_last_part_content(task_payload: dict[str, Any]) -> Any:
    """Extract the last part content from a serialized ProtoLink Task."""
    for collection in ("artifacts", "messages"):
        items = task_payload.get(collection, [])
        if items:
            content = _item_last_part_content(items[-1])
            if content is not None:
                return content
    return None


def _item_last_part_content(item: Any) -> Any:
    """Extract the last part content from a serialized Message or Artifact."""
    if not isinstance(item, dict):
        return None
    parts = item.get("parts", [])
    if not parts:
        return None
    last_part = parts[-1]
    if isinstance(last_part, dict):
        return last_part.get("content")
    return None


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
        path = str(metadata.get("path") or arguments.get("path") or "")
        if path:
            targets.append(path)
        for artifact in action.get("artifacts") or []:
            if not isinstance(artifact, dict) or artifact.get("media_type") != "text/x-diff":
                continue
            for part in artifact.get("parts") or []:
                if not isinstance(part, dict) or not isinstance(part.get("content"), str):
                    continue
                diff = part["content"]
                if diff.strip():
                    diffs.append({"path": path, "diff": diff, "source": "coder"})
    return {"targets": sorted(set(targets)), "diffs": _dedupe_diffs(diffs)}


def _approval_event_summaries(
    requests: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
) -> list[str]:
    """Build concise history entries from typed approval records."""
    by_request = {str(item.get("request_id") or ""): item for item in decisions}
    summaries = []
    for request in requests:
        request_id = str(request.get("request_id") or "")
        raw_action = request.get("action")
        action: dict[str, Any] = raw_action if isinstance(raw_action, dict) else {}
        name = str(action.get("description") or action.get("name") or "runtime action")
        decision = by_request.get(request_id, {})
        outcome = "approved" if decision.get("approved") else "denied"
        summaries.append(f"Approval {outcome}: {name}.")
    return summaries


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
