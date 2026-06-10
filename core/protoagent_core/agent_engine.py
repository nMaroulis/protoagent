"""PyO3-facing entrypoints for the Rust CLI."""

from __future__ import annotations

import json
import os
import platform
import re
import time
from pathlib import Path
from typing import Any

from .agents import agent_manifest
from .config import (
    load_config,
    normalize_provider,
    set_active_model,
    set_api_key,
    visible_config,
)
from .llm import validate_protolink
from .models import discover_models
from .tools import build_context_map, safe_path, workspace_root


def list_models() -> str:
    return _json(discover_models())


def get_config() -> str:
    return _json(visible_config())


def add_api_key(provider: str, api_key: str) -> str:
    return _json(set_api_key(provider, api_key))


def set_model(provider: str, model: str, base_url: str | None = None) -> str:
    return _json(set_active_model(provider, model, base_url))


def doctor(workspace: str | None = None) -> str:
    config = visible_config()
    protolink = validate_protolink()
    inventory = discover_models()
    active_provider = config.get("active_provider", "ollama")
    active = config.get("providers", {}).get(active_provider, {})
    provider_inventory = next(
        (provider for provider in inventory["providers"] if provider["id"] == active_provider),
        None,
    )
    return _json(
        {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "workspace": str(workspace_root(workspace)),
            "config_path": config["config_path"],
            "protolink": protolink,
            "active_provider": active_provider,
            "active_model": active.get("model", ""),
            "active_provider_status": provider_inventory.get("status") if provider_inventory else "unknown",
            "agents": agent_manifest()["agents"],
        }
    )


def process_prompt(prompt: str, workspace: str | None = None) -> str:
    """Process a user prompt and return structured CLI output as JSON.

    The default path is a safe scaffold response so the Rust CLI remains usable
    before local models, provider SDKs, and protolink runtime servers are fully
    debugged. Set PROTOAGENT_LIVE=1 to opt into the experimental live path once
    the Python environment is ready.
    """
    started = time.time()
    workspace = str(workspace_root(workspace))
    os.environ["PROTOAGENT_WORKSPACE"] = workspace

    if os.getenv("PROTOAGENT_LIVE") == "1":
        try:
            return _json(_live_placeholder(prompt, workspace, started))
        except Exception as exc:
            fallback = _fallback_response(prompt, workspace, started)
            fallback["status"] = "fallback"
            fallback["warning"] = f"Live protolink execution failed: {exc}"
            return _json(fallback)

    return _json(_fallback_response(prompt, workspace, started))


def apply_action(action_json: str, workspace: str | None = None) -> str:
    action = json.loads(action_json)
    workspace = str(workspace_root(workspace))
    action_type = action.get("type")
    if action_type != "write_file":
        raise ValueError(f"Unsupported action type: {action_type}")
    path = action.get("path")
    content = action.get("content")
    if not path or content is None:
        raise ValueError("write_file action requires path and content")

    target = safe_path(path, workspace)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return _json(
        {
            "success": True,
            "path": str(target.relative_to(workspace_root(workspace))),
            "bytes_written": len(content.encode("utf-8")),
        }
    )


def _fallback_response(prompt: str, workspace: str, started: float) -> dict[str, Any]:
    config = visible_config()
    provider = config.get("active_provider", "ollama")
    provider_data = config.get("providers", {}).get(provider, {})
    model = provider_data.get("model", "")
    context = build_context_map(workspace)
    targets = _extract_file_targets(prompt, context.get("files", []))
    target_label = ", ".join(targets) if targets else "(not selected yet)"

    events = [
        "Architect received the request and loaded provider configuration.",
        "Explorer built a read-only context map for the workspace.",
        "Coder tools are registered for diff and new-file approval payloads.",
    ]
    if not validate_protolink()["installed"]:
        events.append("Protolink is not importable in this Python environment yet.")
    if not model:
        events.append("No active model is selected for the current provider.")

    thought = (
        f"Request: {prompt}\n\n"
        f"Workspace: {workspace}\n"
        f"Active provider: {provider}\n"
        f"Active model: {model or 'not selected'}\n"
        f"Likely target: {target_label}\n\n"
        "The Rust CLI is now wired to the Python core. Live protolink execution "
        "is intentionally gated behind PROTOAGENT_LIVE=1 so provider/runtime "
        "debugging can happen independently."
    )

    return {
        "status": "ready",
        "headline": "Core scaffold is ready; live mesh is gated.",
        "thought_process": thought,
        "file_target": target_label,
        "diff": "",
        "requires_approval": False,
        "actions": [],
        "events": events,
        "provider": provider,
        "model": model,
        "workspace": workspace,
        "elapsed_ms": int((time.time() - started) * 1000),
    }


def _live_placeholder(prompt: str, workspace: str, started: float) -> dict[str, Any]:
    """Experimental hook where real protolink execution will live.

    Keeping this explicit is useful while protolink is alpha: the CLI can be
    polished and stable while the Python implementation evolves.
    """
    protolink = validate_protolink()
    if not protolink["installed"]:
        raise RuntimeError(protolink["error"])
    config = load_config()
    provider = normalize_provider(config.get("active_provider", "ollama"))
    model = config.get("providers", {}).get(provider, {}).get("model", "")
    return {
        "status": "live-ready",
        "headline": "Live protolink mode reached the execution gate.",
        "thought_process": (
            f"Prompt received for live execution: {prompt}\n"
            f"Provider: {provider}\nModel: {model or 'provider default'}\n"
            "Agent factories are available in protoagent_core.agents."
        ),
        "file_target": "",
        "diff": "",
        "requires_approval": False,
        "actions": [],
        "events": [
            "Validated protolink import.",
            "Loaded active provider and model.",
            "Live HTTP/runtime mesh launch is the next Python debug step.",
        ],
        "provider": provider,
        "model": model,
        "workspace": workspace,
        "elapsed_ms": int((time.time() - started) * 1000),
    }


def _extract_file_targets(prompt: str, files: list[dict[str, Any]]) -> list[str]:
    known = {item.get("path", "") for item in files}
    targets: list[str] = []
    candidates = re.findall(r"[A-Za-z0-9_./-]+\.[A-Za-z0-9_+-]+", prompt)
    for candidate in candidates:
        candidate = candidate.strip("`'\"")
        if candidate in known or Path(candidate).suffix:
            targets.append(candidate)
    return sorted(set(targets))


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=True)
