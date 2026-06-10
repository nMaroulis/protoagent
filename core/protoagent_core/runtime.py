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


def run_selected_model(prompt: str, workspace: str | None = None) -> dict[str, Any]:
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

    return asyncio.run(_run_agent_deck(prompt, provider, model, workspace))


async def _run_agent_deck(
    prompt: str,
    provider: str,
    model: str,
    workspace: str | None,
) -> dict[str, Any]:
    """Start the local ProtoLink mesh and send the prompt to Architect."""
    from protolink.client import AgentClient
    from protolink.discovery import Registry
    from protolink.core.task import Task

    urls = _runtime_urls()
    events: list[str] = [
        f"Registry prepared at {urls['registry']}.",
        f"All LLM-capable agents configured with {provider} / {model}.",
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

        client = AgentClient(url=urls["client"], transport="http", timeout=_runtime_timeout())
        task = Task.create_infer(prompt=prompt)
        events.append("AgentClient sent the user task to Architect.")
        result_task = await client.send_task(agent_url=deck["architect"].card.url, task=task)
        raw_answer = _normalize(result_task.get_last_part_content())
        _collect_side_effects(raw_answer, actions, diffs)

        for payload in side_effects:
            _collect_side_effects(_normalize(payload), actions, diffs)

        answer = _content_to_text(raw_answer)
        if not answer:
            answer = "(model returned an empty response)"

        return {
            "provider": provider,
            "model": model,
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

    action = result.get("action")
    if isinstance(action, dict):
        actions.append(action)

    diff = result.get("diff")
    path = result.get("path") or result.get("file_target") or ""
    if isinstance(diff, str) and diff.strip():
        diffs.append({"path": str(path), "diff": diff})


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
