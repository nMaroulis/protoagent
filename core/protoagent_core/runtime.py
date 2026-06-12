"""ProtoLink runtime mesh for the CLI core."""

from __future__ import annotations

import asyncio
import os
import socket
from contextlib import suppress
from dataclasses import asdict, is_dataclass
from typing import Any

from .agents import create_agent_deck
from .config import load_config, normalize_provider, provider_config

_FALLBACK_PORT = 19100


def run_selected_model(
    prompt: str,
    workspace: str | None = None,
    session_id: str | None = None,
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

    return asyncio.run(_run_agent_deck(prompt, provider, model, workspace, session_id))


async def _run_agent_deck(
    prompt: str,
    provider: str,
    model: str,
    workspace: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    """Start the local ProtoLink mesh and send the prompt to Architect."""
    from protolink.client import AgentClient
    from protolink.discovery import Registry
    from protolink.core.task import Task

    urls = _runtime_urls()
    agent_transport = _agent_transport()
    streaming = _streaming_enabled(agent_transport)
    events: list[str] = [
        f"Registry prepared at {urls['registry']}.",
        f"All LLM-capable agents configured with {provider} / {model}.",
        f"Agent transport: {agent_transport} ({'streaming enabled' if streaming else 'request/response mode'}).",
        f"Active project workspace: {workspace or os.getenv('PROTOAGENT_WORKSPACE', os.getcwd())}.",
        f"Conversation session: {session_id or 'task-local'}."
    ]
    side_effects: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    diffs: list[dict[str, str]] = []
    registry = None
    client = None
    deck: dict[str, Any] = {}
    started_agents: list[Any] = []

    try:
        registry = Registry(url=urls["registry"], transport="http", verbosity=0)
        registry.start(background=True)
        events.append("Registry started.")

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
            side_effects=side_effects,
        )

        for name in ("explorer", "coder", "architect"):
            deck[name].start(background=True)
            started_agents.append(deck[name])
            events.append(f"{name.title()} registered at {deck[name].card.url}.")

        await asyncio.sleep(float(os.getenv("PROTOAGENT_DISCOVERY_DELAY", "0.15")))
        discovered = await deck["architect"].discover_agents()
        names = ", ".join(sorted(card.name for card in discovered)) or "none"
        events.append(f"Architect discovery sees: {names}.")

        client = AgentClient(url=urls["client"], transport=agent_transport, timeout=_runtime_timeout())
        task = Task.create_infer(prompt=prompt)
        if session_id:
            task.metadata["session_id"] = session_id
            task.metadata["workspace"] = workspace or os.getenv("PROTOAGENT_WORKSPACE", os.getcwd())
        if streaming:
            events.append("AgentClient opened a streaming task channel to Architect.")
            try:
                raw_answer = await _send_task_streaming(
                    client=client,
                    agent_url=deck["architect"].card.url,
                    task=task,
                    events=events,
                    actions=actions,
                    diffs=diffs,
                )
            except NotImplementedError as exc:
                events.append(f"Streaming unavailable for {agent_transport}: {exc}")
                events.append("Falling back to request/response task execution.")
                raw_answer = await _send_task_once(client, deck["architect"].card.url, task)
            except Exception as exc:
                events.append(f"Streaming task path failed: {exc}")
                events.append("Falling back to request/response task execution.")
                raw_answer = await _send_task_once(client, deck["architect"].card.url, task)
        else:
            events.append("AgentClient sent the user task to Architect.")
            raw_answer = await _send_task_once(client, deck["architect"].card.url, task)

        _collect_side_effects(raw_answer, actions, diffs)

        for payload in side_effects:
            source = str(payload.get("source", "agent")).title() if isinstance(payload, dict) else "Agent"
            path = ""
            if isinstance(payload, dict):
                path = str(payload.get("path") or payload.get("file_target") or "")
            events.append(f"{source} produced approval metadata{f' for {path}' if path else ''}.")
            _collect_side_effects(_normalize(payload), actions, diffs)

        answer = _content_to_text(raw_answer)
        if not answer:
            answer = "(model returned an empty response)"

        return {
            "provider": provider,
            "model": model,
            "responder": "architect",
            "answer": answer,
            "events": events,
            "actions": actions,
            "diffs": _dedupe_diffs(diffs),
        }
    finally:
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
        "architect": _env_url("PROTOAGENT_ARCHITECT_URL", "ARCHITECT_AGENT_URL") or _local_url(host),
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


def _agent_transport() -> str:
    """Return the ProtoLink transport used by local agents and the client."""
    transport = os.getenv("PROTOAGENT_AGENT_TRANSPORT", "sse").strip().lower()
    aliases = {
        "jsonrpc": "sse",
        "json-rpc": "sse",
        "sse-jsonrpc": "sse",
        "sse-json-rpc": "sse",
    }
    return aliases.get(transport, transport or "sse")


def _streaming_enabled(transport: str) -> bool:
    """Decide whether to consume ProtoLink task streams for this run."""
    raw = os.getenv("PROTOAGENT_STREAM", "1").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return transport != "http"
    return transport != "http"


async def _send_task_once(client, agent_url: str, task) -> Any:
    """Send a task through ProtoLink's request/response client path."""
    result_task = await client.send_task(agent_url=agent_url, task=task)
    return _normalize(result_task.get_last_part_content())


async def _send_task_streaming(
    *,
    client,
    agent_url: str,
    task,
    events: list[str],
    actions: list[dict[str, Any]],
    diffs: list[dict[str, str]],
) -> Any:
    """Consume ProtoLink streaming events and return the final answer payload."""
    final_task: dict[str, Any] | None = None
    final_content: Any = None
    artifact_content: Any = None

    async for event in client.send_task_streaming(agent_url=agent_url, task=task):
        payload = _normalize(event)
        if not isinstance(payload, dict):
            _append_event(events, f"Stream event: {_content_to_text(payload)}")
            continue

        summary = _stream_event_summary(payload)
        if summary:
            _append_event(events, summary)

        event_type = payload.get("type")
        if event_type == "task_error":
            raise RuntimeError(payload.get("error_message") or "Agent stream returned an error")

        if event_type == "task_status_update":
            metadata = payload.get("metadata", {})
            if payload.get("final") and isinstance(metadata, dict) and isinstance(metadata.get("task"), dict):
                final_task = metadata["task"]
            continue

        if event_type == "task_artifact_update":
            artifact = payload.get("artifact")
            artifact_content = _item_last_part_content(artifact)
            _collect_side_effects(artifact_content, actions, diffs)
            continue

        if event_type == "task_llm_stream":
            content = payload.get("content")
            metadata = payload.get("metadata", {})
            if isinstance(metadata, dict):
                _collect_side_effects(metadata.get("result"), actions, diffs)
            _collect_side_effects(content, actions, diffs)
            if payload.get("llm_event_type") == "llm_final" or payload.get("final"):
                final_content = content

    if final_task:
        task_content = _task_last_part_content(final_task)
        if task_content is not None:
            return _normalize(task_content)
    if artifact_content is not None:
        return _normalize(artifact_content)
    return _normalize(final_content)


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


def _append_event(events: list[str], message: str) -> None:
    """Append a trace event without letting token streams flood the CLI."""
    raw_limit = os.getenv("PROTOAGENT_STREAM_TRACE_LIMIT", "120")
    try:
        limit = max(20, int(raw_limit))
    except ValueError:
        limit = 120
    if len(events) < limit:
        events.append(message)
    elif not events[-1].startswith("Stream trace limit reached"):
        events.append(f"Stream trace limit reached ({limit}); suppressing further event summaries.")


def _stream_event_summary(event: dict[str, Any]) -> str:
    """Build a compact human-readable summary for a ProtoLink stream event."""
    event_type = event.get("type", "")
    if event_type == "task_status_update":
        previous = event.get("previous_state") or "none"
        current = event.get("new_state") or "unknown"
        suffix = " (final)" if event.get("final") else ""
        return f"Task state: {previous} -> {current}{suffix}."
    if event_type == "task_progress":
        message = event.get("message") or "progress update"
        return f"Progress: {message}."
    if event_type == "task_artifact_update":
        return "Architect emitted a task artifact."
    if event_type == "task_error":
        return f"Task error: {event.get('error_message') or 'unknown error'}."
    if event_type != "task_llm_stream":
        return ""

    agent = event.get("agent_name") or "agent"
    kind = event.get("llm_event_type") or "event"
    step = event.get("step")
    step_label = f" step {step}" if step is not None else ""
    metadata = event.get("metadata", {}) if isinstance(event.get("metadata"), dict) else {}

    if kind == "llm_chunk":
        return ""
    if kind == "llm_step":
        return f"{agent}{step_label}: LLM step started."
    if kind == "llm_response":
        mode = "streamed" if metadata.get("streaming") else "received"
        return f"{agent}{step_label}: LLM response {mode}."
    if kind == "llm_action":
        return f"{agent}{step_label}: selected action {metadata.get('action', 'unknown')}."
    if kind == "tool_start":
        return f"{agent}{step_label}: calling tool {metadata.get('tool', 'unknown')}."
    if kind == "tool_result":
        return f"{agent}{step_label}: tool {metadata.get('tool', 'unknown')} returned."
    if kind == "agent_call_start":
        return (
            f"{agent}{step_label}: delegating to "
            f"{metadata.get('agent', 'unknown')} ({metadata.get('action', 'infer')})."
        )
    if kind == "agent_call_result":
        return f"{agent}{step_label}: delegation from {metadata.get('agent', 'unknown')} returned."
    if kind == "llm_final":
        return f"{agent}{step_label}: final response produced."
    if kind in {"llm_parse_error", "llm_error", "tool_error", "agent_call_error"}:
        return f"{agent}{step_label}: {kind.replace('_', ' ')}: {metadata.get('message', 'unknown error')}."
    return f"{agent}{step_label}: {kind.replace('_', ' ')}."


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


def _collect_side_effects(
    result: Any,
    actions: list[dict[str, Any]],
    diffs: list[dict[str, str]],
) -> None:
    """Collect approval actions and diffs from nested tool payloads."""
    if isinstance(result, list):
        for item in result:
            _collect_side_effects(item, actions, diffs)
        return
    if not isinstance(result, dict):
        return

    nested = result.get("result")
    if nested is not None:
        _collect_side_effects(nested, actions, diffs)

    source = str(result.get("source", "")).strip()
    action = result.get("action")
    if isinstance(action, dict):
        if action.get("type") == "write_file":
            enriched = dict(action)
            if source:
                enriched.setdefault("source", source)
            actions.append(enriched)

    diff = result.get("diff")
    path = result.get("path") or result.get("file_target") or ""
    if isinstance(diff, str) and diff.strip():
        item = {"path": str(path), "diff": diff}
        if source:
            item["source"] = source
        diffs.append(item)


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
