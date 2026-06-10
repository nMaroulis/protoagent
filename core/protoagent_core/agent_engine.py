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
    set_active_model,
    set_api_key,
    visible_config,
)
from .llm import validate_protolink
from .models import discover_models
from .runtime import run_selected_model
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

    By default this calls the selected provider/model. Set
    PROTOAGENT_SCAFFOLD=1 to force the old no-model scaffold mode.
    """
    started = time.time()
    workspace = str(workspace_root(workspace))
    os.environ["PROTOAGENT_WORKSPACE"] = workspace

    if os.getenv("PROTOAGENT_SCAFFOLD") == "1":
        return _json(_fallback_response(prompt, workspace, started))

    try:
        return _json(_model_response(prompt, workspace, started))
    except Exception as exc:
        fallback = _fallback_response(prompt, workspace, started)
        fallback["status"] = "fallback"
        fallback["headline"] = "ProtoLink agent run failed; showing core diagnostics."
        fallback["warning"] = str(exc)
        fallback["events"].append(f"ProtoLink agent run failed: {exc}")
        return _json(fallback)


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
    protolink_status = validate_protolink()
    if not protolink_status["installed"]:
        events.append("Protolink is not importable in this Python environment yet.")
    elif not protolink_status.get("agent_ready"):
        events.append(f"Protolink Agent runtime is blocked: {protolink_status.get('error', 'unknown error')}")
    if not model:
        events.append("No active model is selected for the current provider.")

    thought = (
        f"Request: {prompt}\n\n"
        f"Workspace: {workspace}\n"
        f"Active provider: {provider}\n"
        f"Active model: {model or 'not selected'}\n"
        f"Likely target: {target_label}\n\n"
        "The Python core could not complete the ProtoLink agent run, so this "
        "diagnostic response shows the selected runtime, workspace, and "
        "registered tools."
    )

    return {
        "status": "ready",
        "headline": "Core diagnostics are available.",
        "answer": "",
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


def _model_response(prompt: str, workspace: str, started: float) -> dict[str, Any]:
    result = run_selected_model(prompt, workspace)
    context = build_context_map(workspace)
    targets = _extract_file_targets(prompt, context.get("files", []))
    diff_items = result.get("diffs", [])
    action_items = result.get("actions", [])
    action_targets = [
        str(action.get("path", ""))
        for action in action_items
        if isinstance(action, dict) and action.get("path")
    ]
    diff_targets = [
        str(item.get("path", ""))
        for item in diff_items
        if isinstance(item, dict) and item.get("path")
    ]
    target_label = ", ".join(sorted(set([*targets, *action_targets, *diff_targets]))) if [*targets, *action_targets, *diff_targets] else ""
    diff = "\n".join(str(item.get("diff", "")) for item in diff_items if isinstance(item, dict))
    return {
        "status": "approval-required" if action_items else "answered",
        "headline": "Architect completed the ProtoLink run.",
        "answer": result["answer"],
        "thought_process": (
            f"Request: {prompt}\n\n"
            f"Workspace: {workspace}\n"
            f"Active provider: {result['provider']}\n"
            f"Active model: {result['model']}\n"
            f"Likely target: {target_label or '(not selected yet)'}"
        ),
        "file_target": target_label,
        "diff": diff,
        "requires_approval": bool(action_items),
        "actions": action_items,
        "events": result.get("events", []),
        "provider": result["provider"],
        "model": result["model"],
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
