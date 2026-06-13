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
from .tools import build_context_map, create_new_file, list_directory, read_file, safe_path, workspace_root


MAX_TAGGED_FILES = 8
MAX_TAGGED_CONTEXT_CHARS = 80_000
MAX_TAGGED_ITEM_CHARS = 24_000


def list_models() -> str:
    """Return model inventory JSON for the Rust CLI."""
    return _json(discover_models())


def get_config() -> str:
    """Return redacted provider configuration JSON for display."""
    return _json(visible_config())


def add_api_key(provider: str, api_key: str) -> str:
    """Store a cloud provider API key and return redacted config JSON."""
    return _json(set_api_key(provider, api_key))


def set_model(provider: str, model: str, base_url: str | None = None) -> str:
    """Persist the active provider/model selection and return config JSON."""
    return _json(set_active_model(provider, model, base_url))


def doctor(workspace: str | None = None) -> str:
    """Return runtime diagnostics consumed by the CLI doctor panel."""
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


def process_prompt(
    prompt: str,
    workspace: str | None = None,
    session_id: str | None = None,
    progress_path: str | None = None,
) -> str:
    """Process a user prompt and return structured CLI output as JSON.

    By default this calls the selected provider/model. Set
    PROTOAGENT_SCAFFOLD=1 to force the old no-model scaffold mode.
    """
    started = time.time()
    workspace = str(workspace_root(workspace))
    os.environ["PROTOAGENT_WORKSPACE"] = workspace
    _emit_progress(progress_path, f"CLI accepted task for workspace {workspace}.")
    _emit_progress(progress_path, "Resolving tagged file context from the prompt.")
    tagged_context = _tagged_file_context(prompt, workspace)
    for event in _tag_events(tagged_context):
        _emit_progress(progress_path, event)

    if os.getenv("PROTOAGENT_SCAFFOLD") == "1":
        _emit_progress(progress_path, "Scaffold mode selected; returning diagnostics without a model call.")
        return _json(_fallback_response(prompt, workspace, started, tagged_context, progress_path))

    try:
        return _json(_model_response(prompt, workspace, started, tagged_context, session_id, progress_path))
    except Exception as exc:
        _emit_progress(progress_path, f"ProtoLink agent run failed: {exc}")
        fallback = _fallback_response(prompt, workspace, started, tagged_context, progress_path)
        fallback["status"] = "fallback"
        fallback["headline"] = "ProtoLink agent run failed; showing core diagnostics."
        fallback["warning"] = str(exc)
        fallback["events"].append(f"ProtoLink agent run failed: {exc}")
        return _json(fallback)


def apply_action(action_json: str, workspace: str | None = None) -> str:
    """Apply a user-approved action payload inside the workspace."""
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


def _fallback_response(
    prompt: str,
    workspace: str,
    started: float,
    tagged_context: dict[str, Any] | None = None,
    progress_path: str | None = None,
) -> dict[str, Any]:
    """Build a diagnostic response when the live ProtoLink run is unavailable."""
    tagged_context = tagged_context or {"items": [], "errors": []}
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
    events.extend(_tag_events(tagged_context))
    for event in events:
        _emit_progress(progress_path, event)

    thought = (
        f"Request: {prompt}\n\n"
        f"Workspace: {workspace}\n"
        f"Active provider: {provider}\n"
        f"Active model: {model or 'not selected'}\n"
        f"Likely target: {target_label}\n\n"
        f"Tagged context: {_tag_summary(tagged_context)}\n\n"
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
        "responder": "architect",
        "workspace": workspace,
        "warning": "; ".join(tagged_context.get("errors", [])),
        "elapsed_ms": int((time.time() - started) * 1000),
    }


def _model_response(
    prompt: str,
    workspace: str,
    started: float,
    tagged_context: dict[str, Any] | None = None,
    session_id: str | None = None,
    progress_path: str | None = None,
) -> dict[str, Any]:
    """Run the live model path and adapt the result to the CLI response schema."""
    tagged_context = tagged_context or {"items": [], "errors": []}
    runtime_prompt = _prompt_with_tagged_context(prompt, tagged_context)
    result = run_selected_model(runtime_prompt, workspace, session_id, progress_path)
    context = build_context_map(workspace)
    targets = _extract_file_targets(prompt, context.get("files", []))
    diff_items = result.get("diffs", [])
    action_items = result.get("actions", [])
    repair_events: list[str] = []
    answer = result["answer"]
    if not action_items:
        repaired = _repair_missing_create_action(prompt, answer, workspace)
        if repaired.get("success"):
            action = dict(repaired["action"])
            action.setdefault("source", "coder")
            action_items = [action]
            diff_items = [*diff_items, {"path": repaired["path"], "diff": repaired["diff"], "source": "coder"}]
            repair_events.append(
                f"Coder safety net converted code-only create response into approval action for {repaired['path']}."
            )
            _emit_progress(progress_path, repair_events[-1])
            answer = (
                answer.rstrip()
                + f"\n\nPrepared an approval-gated file creation for `{repaired['path']}`."
            )
        elif repaired.get("error"):
            repair_events.append(f"Create-action safety net did not run: {repaired['error']}")
            _emit_progress(progress_path, repair_events[-1])
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
        "answer": answer,
        "thought_process": (
            f"Request: {prompt}\n\n"
            f"Workspace: {workspace}\n"
            f"Active provider: {result['provider']}\n"
            f"Active model: {result['model']}\n"
            f"Conversation session: {session_id or 'task-local'}\n"
            f"Likely target: {target_label or '(not selected yet)'}\n"
            f"Tagged context: {_tag_summary(tagged_context)}"
        ),
        "file_target": target_label,
        "diff": diff,
        "requires_approval": bool(action_items),
        "actions": action_items,
        "events": [*result.get("events", []), *repair_events, *_tag_events(tagged_context)],
        "provider": result["provider"],
        "model": result["model"],
        "responder": result.get("responder", "architect"),
        "workspace": workspace,
        "warning": "; ".join(tagged_context.get("errors", [])),
        "elapsed_ms": int((time.time() - started) * 1000),
    }


def _tagged_file_context(prompt: str, workspace: str) -> dict[str, Any]:
    """Resolve @file references into bounded read-only prompt context."""
    tags = _extract_file_tags(prompt)
    items: list[dict[str, str]] = []
    errors: list[str] = []
    remaining = MAX_TAGGED_CONTEXT_CHARS

    for tag in tags[:MAX_TAGGED_FILES]:
        try:
            target = safe_path(tag, workspace)
        except ValueError as exc:
            errors.append(str(exc))
            continue

        if not target.exists():
            errors.append(f"Tagged path not found: @{tag}")
            continue

        if target.is_dir():
            listing = list_directory(tag, workspace)
            if not listing.get("success"):
                errors.append(str(listing.get("error", f"Could not list @{tag}")))
                continue
            body = "\n".join(
                f"- {entry.get('path')} ({entry.get('type')})"
                for entry in listing.get("entries", [])[:80]
            )
            kind = "directory"
        else:
            loaded = read_file(tag, workspace, with_line_numbers=True)
            if not loaded.get("success"):
                errors.append(str(loaded.get("error", f"Could not read @{tag}")))
                continue
            body = str(loaded.get("content", ""))
            kind = "file"

        if not body:
            body = "(empty)"
        body = body[: min(MAX_TAGGED_ITEM_CHARS, remaining)]
        remaining -= len(body)
        items.append({"path": tag, "kind": kind, "content": body})
        if remaining <= 0:
            errors.append("Tagged context limit reached; remaining @ files were skipped.")
            break

    if len(tags) > MAX_TAGGED_FILES:
        errors.append(f"Only the first {MAX_TAGGED_FILES} @ files were loaded.")

    return {"items": items, "errors": errors}


def _extract_file_tags(prompt: str) -> list[str]:
    """Return ordered unique @file references from a prompt."""
    tags: list[str] = []
    seen: set[str] = set()
    pattern = re.compile(r'(?<!\S)@(?:"((?:[^"\\]|\\.)+)"|([^\s]+))')
    for match in pattern.finditer(prompt):
        raw = match.group(1) or match.group(2) or ""
        tag = raw.replace(r"\"", '"').rstrip(".,;:)]}")
        if tag and tag not in seen:
            tags.append(tag)
            seen.add(tag)
    return tags


def _prompt_with_tagged_context(prompt: str, tagged_context: dict[str, Any]) -> str:
    items = tagged_context.get("items", [])
    if not items:
        return prompt
    sections = [
        prompt,
        "",
        "Tagged file context selected by the user with @. Treat it as read-only context unless the user asks for changes.",
    ]
    for item in items:
        sections.extend(
            [
                "",
                f"--- @{item['path']} ({item['kind']}) ---",
                item["content"],
            ]
        )
    return "\n".join(sections)


def _tag_summary(tagged_context: dict[str, Any]) -> str:
    items = tagged_context.get("items", [])
    if not items:
        return "none"
    return ", ".join(f"@{item['path']}" for item in items)


def _tag_events(tagged_context: dict[str, Any]) -> list[str]:
    events = [
        f"Loaded tagged {item['kind']} context from @{item['path']}."
        for item in tagged_context.get("items", [])
    ]
    events.extend(f"Tagged context warning: {error}" for error in tagged_context.get("errors", []))
    return events


def _repair_missing_create_action(prompt: str, answer: str, workspace: str) -> dict[str, Any]:
    """Convert obvious code-only create responses into approval-gated actions."""
    request = _latest_user_request(prompt)
    if not _looks_like_create_request(request):
        return {"success": False}

    content = _first_code_block(answer) or _first_code_block(prompt) or _print_script_from_request(request)
    if not content:
        return {"success": False, "error": "no code content found"}

    path = (
        _first_path_candidate(request)
        or _first_path_candidate(answer)
        or _first_path_candidate(prompt)
        or _default_script_path(request, content)
    )
    try:
        proposal = create_new_file(path, content, workspace)
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    if not proposal.get("success"):
        return {"success": False, "error": str(proposal.get("error", "proposal failed"))}
    return proposal


def _latest_user_request(prompt: str) -> str:
    marker = "Current user request:"
    if marker in prompt:
        return prompt.rsplit(marker, 1)[-1].strip()
    return prompt


def _looks_like_create_request(text: str) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in ("create", "make", "write", "add")) and any(
        word in lowered for word in ("file", "script", "program")
    )


def _first_code_block(text: str) -> str:
    match = re.search(r"```(?:[A-Za-z0-9_+.-]+)?\s*\n(.*?)```", text, flags=re.DOTALL)
    if not match:
        return ""
    content = match.group(1).strip("\n")
    return content + ("\n" if content and not content.endswith("\n") else "")


def _print_script_from_request(text: str) -> str:
    match = re.search(r"prints?\s+['\"]?([A-Za-z0-9 _.-]+)['\"]?", text, flags=re.IGNORECASE)
    if not match:
        return ""
    value = match.group(1).strip()
    if not value:
        return ""
    return f'print("{value}")\n'


def _first_path_candidate(text: str) -> str:
    candidates = re.findall(r"(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.[A-Za-z0-9_+-]+", text)
    for candidate in candidates:
        candidate = candidate.strip("`'\"")
        if candidate and not candidate.startswith(("http://", "https://")):
            return candidate
    return ""


def _default_script_path(request: str, content: str) -> str:
    if "print(\"abc\")" in content or "print('abc')" in content or "abc" in request.lower():
        return "scripts/print_abc.py"
    words = re.findall(r"[A-Za-z0-9]+", request.lower())
    stem_words = [word for word in words if word not in {"create", "make", "write", "add", "a", "an", "the", "file", "script", "program", "that"}]
    stem = "_".join(stem_words[:4]) or "generated_script"
    return f"scripts/{stem}.py"


def _extract_file_targets(prompt: str, files: list[dict[str, Any]]) -> list[str]:
    """Infer likely file targets mentioned in a prompt."""
    known = {item.get("path", "") for item in files}
    targets: list[str] = []
    candidates = re.findall(r"[A-Za-z0-9_./-]+\.[A-Za-z0-9_+-]+", prompt)
    for candidate in candidates:
        candidate = candidate.strip("`'\"")
        if candidate in known or Path(candidate).suffix:
            targets.append(candidate)
    return sorted(set(targets))


def _json(value: dict[str, Any]) -> str:
    """Serialize a response using ASCII-safe JSON for the Rust boundary."""
    return json.dumps(value, ensure_ascii=True)


def _emit_progress(progress_path: str | None, message: str) -> None:
    """Best-effort JSONL progress emitter for the TUI."""
    if not progress_path:
        return
    try:
        with open(progress_path, "a", encoding="utf-8") as handle:
            handle.write(
                json.dumps({"ts": time.time(), "event": message}, ensure_ascii=True) + "\n"
            )
            handle.flush()
    except OSError:
        pass
