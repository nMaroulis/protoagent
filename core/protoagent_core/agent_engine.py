"""PyO3-facing entrypoints for the Rust CLI."""

from __future__ import annotations

import json
import os
import platform
import re
import time
from pathlib import Path
from typing import Any

from .config import (
    LOCAL_PROVIDERS,
    MAX_CONTEXT_WINDOW,
    MIN_CONTEXT_WINDOW,
    provider_config,
    set_active_model,
    set_agent_prompt_profile,
    set_api_key,
    set_context_window,
    visible_config,
)
from .context import (
    build_context_pack,
    context_pack_events,
    context_pack_summary,
    format_context_pack_for_prompt,
    refresh_context_index,
)
from .context import (
    context_status as loom_status,
)
from .llm import ollama_context_window_details, validate_protolink
from .models import discover_models, remember_valid_provider
from .prompt_profiles import prompt_profile_status
from .tools import build_context_map, list_directory, read_file, safe_path, workspace_root

MAX_TAGGED_FILES = 6
MAX_TAGGED_CONTEXT_CHARS = 12_000
MAX_TAGGED_ITEM_CHARS = 6_000
LOCAL_RUNTIME_CONTEXT_CHARS = 6_000
REMOTE_RUNTIME_CONTEXT_CHARS = 48_000


def list_models(validate_api_keys: bool = False) -> str:
    """Return model inventory JSON for the Rust CLI."""
    return _json(discover_models(validate_api_keys=validate_api_keys))


def get_config() -> str:
    """Return redacted provider configuration JSON for display."""
    return _json(visible_config())


def add_api_key(provider: str, api_key: str) -> str:
    """Store a cloud provider API key and return redacted config JSON."""
    return _json(set_api_key(provider, api_key))


def set_model(provider: str, model: str, base_url: str | None = None) -> str:
    """Persist the active provider/model selection and return config JSON."""
    return _json(set_active_model(provider, model, base_url))


def answer_help_question(question: str) -> str:
    """Answer a ProtoAgent usage question through the isolated Guide agent."""
    from .help_agent import answer_help_question as guide_answer_help_question

    return _json(guide_answer_help_question(question))


def get_context_settings() -> str:
    """Return the active model's application-managed context settings."""
    config = visible_config()
    provider = str(config.get("active_provider", "ollama"))
    active = config.get("providers", {}).get(provider, {})
    if provider != "ollama":
        return _json(
            {
                "provider": provider,
                "model": active.get("model", ""),
                "controllable": False,
                "window_tokens": None,
                "configured_tokens": active.get("context_window"),
                "source": "provider managed",
            }
        )
    details = ollama_context_window_details(provider_config(provider))
    return _json(
        {
            "provider": provider,
            "model": active.get("model", ""),
            "controllable": True,
            **details,
        }
    )


def configure_context_window(value: str | int | None = None) -> str:
    """Set the active Ollama context window, accepting values such as ``16k``."""
    config = visible_config()
    provider = str(config.get("active_provider", "ollama"))
    window_tokens = _parse_context_window(value)
    set_context_window(provider, window_tokens)
    return get_context_settings()


def get_agent_prompt_profile() -> str:
    """Return the active agent prompt profile and resolved model tier."""
    config = visible_config()
    return _json(prompt_profile_status(config))


def configure_agent_prompt_profile(value: str | None = None) -> str:
    """Set the active agent prompt profile, or reset it to auto."""
    config = set_agent_prompt_profile(value)
    return _json(prompt_profile_status(config))


def run_quality_eval(
    mode: str = "scaffold",
    profiles: str | None = None,
    task_ids: str | None = None,
    limit: int | None = None,
    workspace: str | None = None,
) -> str:
    """Run the built-in prompt-profile quality evaluation harness."""
    from .quality_eval import run_quality_eval as run_eval

    return _json(
        run_eval(
            workspace=workspace,
            profiles=profiles,
            task_ids=task_ids,
            mode=mode,
            limit=limit,
        )
    )


def list_quality_eval_tasks() -> str:
    """Return the built-in prompt-profile quality evaluation tasks."""
    from .quality_eval import list_eval_tasks

    return _json(list_eval_tasks())


def compact_protolink_history(
    session_id: str,
    strategy: str = "tokens",
    limit: int | None = None,
) -> str:
    """Compact the current per-agent session through ProtoLink state APIs."""
    strategy = strategy.strip().lower()
    if limit is not None and limit < (2 if strategy == "recent" else 1):
        minimum = 2 if strategy == "recent" else 1
        raise ValueError(f"{strategy} compaction limit must be at least {minimum}")
    config = visible_config()
    provider = str(config.get("active_provider", "ollama"))
    model = str(config.get("providers", {}).get(provider, {}).get("model", "")) or None
    from .history import compact_saved_histories

    result = compact_saved_histories(
        session_id,
        provider,
        model,
        strategy=strategy,
        limit=limit,
    )
    changed_agents = [
        str(report.get("agent", "agent")).title()
        for report in result["agents"]
        if report.get("changed")
    ]
    if result["errors"]:
        result["summary"] = "ProtoLink compaction completed with warnings: " + "; ".join(
            result["errors"]
        )
    elif changed_agents:
        names = ", ".join(changed_agents)
        result["summary"] = (
            f"ProtoLink {strategy} compaction updated {names}: "
            f"removed {result['removed_messages']} message(s)."
        )
    elif result["found"]:
        result["summary"] = (
            "ProtoLink histories were already within the requested compaction boundary."
        )
    else:
        result["summary"] = "No saved ProtoLink conversation history exists for this project."
    return _json(result)


def reset_protolink_history(session_id: str) -> str:
    """Clear the current ProtoLink session across Architect, Explorer, and Coder."""
    from .history import reset_saved_histories

    result = reset_saved_histories(session_id)
    cleared = [str(name).title() for name in result["cleared_agents"]]
    result["summary"] = (
        f"Cleared ProtoLink conversation history for {', '.join(cleared)}."
        if cleared
        else "No saved ProtoLink conversation history exists for this project."
    )
    return _json(result)


def describe_protolink_history(session_id: str) -> str:
    """Return a read-only summary of the current ProtoLink conversation memory."""
    from .history import describe_saved_histories

    result = describe_saved_histories(session_id)
    result["summary"] = (
        "Saved ProtoLink conversation history for this project."
        if result["found"]
        else "No saved ProtoLink conversation history exists for this project."
    )
    return _json(result)


def _parse_context_window(value: str | int | None) -> int | None:
    if value is None:
        return None
    raw = str(value).strip().lower().replace("_", "")
    if raw in {"", "auto", "default"}:
        return None
    multiplier = 1
    if raw.endswith("k"):
        raw = raw[:-1]
        multiplier = 1_024
    elif raw.endswith("m"):
        raw = raw[:-1]
        multiplier = 1_048_576
    try:
        parsed = int(raw) * multiplier
    except ValueError as exc:
        raise ValueError("Context window must be a token count such as 8192 or 16k") from exc
    if not MIN_CONTEXT_WINDOW <= parsed <= MAX_CONTEXT_WINDOW:
        raise ValueError(
            f"Context window must be between {MIN_CONTEXT_WINDOW} and {MAX_CONTEXT_WINDOW} tokens"
        )
    return parsed


def doctor(workspace: str | None = None) -> str:
    """Return runtime diagnostics consumed by the CLI doctor panel."""
    from .agents import agent_manifest

    config = visible_config()
    protolink = validate_protolink()
    inventory = discover_models()
    active_provider = config.get("active_provider", "ollama")
    active = config.get("providers", {}).get(active_provider, {})
    profile = prompt_profile_status(config, provider=str(active_provider), model=active.get("model", ""))
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
            "active_provider_status": provider_inventory.get("status")
            if provider_inventory
            else "unknown",
            "prompt_profile": profile,
            "agents": agent_manifest(profile)["agents"],
        }
    )


def context_status(workspace: str | None = None) -> str:
    """Return Context Loom index status for the active workspace."""
    return _json(loom_status(str(workspace_root(workspace))))


def refresh_context(workspace: str | None = None) -> str:
    """Refresh the Context Loom index for the active workspace."""
    return _json(refresh_context_index(str(workspace_root(workspace))))


def context_pack(query: str = "", workspace: str | None = None) -> str:
    """Build a Context Loom pack for a query without running the model."""
    workspace = str(workspace_root(workspace))
    return _json(
        build_context_pack(
            query,
            workspace,
            tagged_paths=_extract_file_tags(query),
        )
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
    _emit_progress(progress_path, "Weaving Context Loom pack from the active workspace.")
    loom_context = _context_pack_for_prompt(prompt, workspace, tagged_context)
    for event in _context_events(loom_context):
        _emit_progress(progress_path, event)

    if os.getenv("PROTOAGENT_SCAFFOLD") == "1":
        _emit_progress(
            progress_path, "Scaffold mode selected; returning diagnostics without a model call."
        )
        return _json(
            _fallback_response(
                prompt,
                workspace,
                started,
                tagged_context,
                progress_path,
                loom_context,
            )
        )

    try:
        return _json(
            _model_response(
                prompt,
                workspace,
                started,
                tagged_context,
                session_id,
                progress_path,
                loom_context,
            )
        )
    except Exception as exc:
        _emit_progress(progress_path, f"ProtoLink agent run failed: {exc}")
        fallback = _fallback_response(
            prompt,
            workspace,
            started,
            tagged_context,
            progress_path,
            loom_context,
        )
        fallback["status"] = "fallback"
        fallback["headline"] = "ProtoLink agent run failed; showing core diagnostics."
        fallback["warning"] = str(exc)
        fallback["events"].append(f"ProtoLink agent run failed: {exc}")
        return _json(fallback)


def _fallback_response(
    prompt: str,
    workspace: str,
    started: float,
    tagged_context: dict[str, Any] | None = None,
    progress_path: str | None = None,
    loom_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a diagnostic response when the live ProtoLink run is unavailable."""
    tagged_context = tagged_context or {"items": [], "errors": []}
    config = visible_config()
    provider = config.get("active_provider", "ollama")
    provider_data = config.get("providers", {}).get(provider, {})
    model = provider_data.get("model", "")
    profile = prompt_profile_status(config, provider=str(provider), model=str(model))
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
        events.append(
            f"Protolink Agent runtime is blocked: {protolink_status.get('error', 'unknown error')}"
        )
    if not model:
        events.append("No active model is selected for the current provider.")
    events.extend(_tag_events(tagged_context))
    events.extend(_context_events(loom_context))
    for event in events:
        _emit_progress(progress_path, event)

    thought = (
        f"Request: {prompt}\n\n"
        f"Workspace: {workspace}\n"
        f"Active provider: {provider}\n"
        f"Active model: {model or 'not selected'}\n"
        f"Prompt profile: {profile['label']} (configured {profile['configured']}, resolved {profile['resolved']})\n"
        f"Likely target: {target_label}\n\n"
        f"Tagged context: {_tag_summary(tagged_context)}\n\n"
        f"Context Loom: {_context_summary(loom_context)}\n\n"
        "Conversation memory: ProtoLink per-agent session state\n\n"
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
        "events": events,
        "run_events": [],
        "approval_requests": [],
        "approval_decisions": [],
        "run_context": {},
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
    loom_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the live model path and adapt the result to the CLI response schema."""
    from .history import persist_architect_turn
    from .runtime import run_selected_model

    tagged_context = tagged_context or {"items": [], "errors": []}
    runtime_prompt = _runtime_prompt(prompt, tagged_context, loom_context)
    result = run_selected_model(runtime_prompt, workspace, session_id, progress_path)
    runtime_status = str(result.get("status") or "completed")
    if runtime_status == "canceled":
        targets = []
    else:
        remember_valid_provider(result["provider"], result["model"])
        context = build_context_map(workspace)
        targets = _extract_file_targets(prompt, context.get("files", []))
    diff_items = result.get("diffs", [])
    answer = result["answer"]
    profile = result.get("prompt_profile", {})
    profile_label = str(profile.get("label") or profile.get("resolved") or "unknown")
    profile_configured = str(profile.get("configured") or "auto")
    profile_resolved = str(profile.get("resolved") or "unknown")
    action_targets = [str(path) for path in result.get("targets", []) if path]
    diff_targets = [
        str(item.get("path", ""))
        for item in diff_items
        if isinstance(item, dict) and item.get("path")
    ]
    if runtime_status != "canceled":
        persist_architect_turn(
            session_id,
            workspace=workspace,
            user_prompt=prompt,
            assistant_answer=answer,
        )
    target_label = (
        ", ".join(sorted(set([*targets, *action_targets, *diff_targets])))
        if [*targets, *action_targets, *diff_targets]
        else ""
    )
    diff = "\n".join(str(item.get("diff", "")) for item in diff_items if isinstance(item, dict))
    return {
        "status": "canceled" if runtime_status == "canceled" else "answered",
        "headline": "Architect completed the ProtoLink run.",
        "answer": answer,
        "thought_process": (
            f"Request: {prompt}\n\n"
            f"Workspace: {workspace}\n"
            f"Active provider: {result['provider']}\n"
            f"Active model: {result['model']}\n"
            f"Prompt profile: {profile_label} (configured {profile_configured}, resolved {profile_resolved})\n"
            f"Conversation session: {session_id or 'task-local'}\n"
            f"Likely target: {target_label or '(not selected yet)'}\n"
            f"Context Loom: {_context_summary(loom_context)}\n"
            f"Tagged context: {_tag_summary(tagged_context)}\n"
            "Conversation memory: ProtoLink per-agent session state"
        ),
        "file_target": target_label,
        "diff": diff,
        "events": [
            *result.get("events", []),
            *_context_events(loom_context),
            *_tag_events(tagged_context),
        ],
        "run_events": result.get("run_events", []),
        "approval_requests": result.get("approval_requests", []),
        "approval_decisions": result.get("approval_decisions", []),
        "run_context": result.get("run_context", {}),
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


def _runtime_prompt(
    prompt: str,
    tagged_context: dict[str, Any],
    loom_context: dict[str, Any] | None = None,
) -> str:
    tagged_items = tagged_context.get("items", [])
    loom_prompt = format_context_pack_for_prompt(loom_context or {})
    if not tagged_items and not loom_prompt:
        return prompt

    sections = [
        "Use Context Loom and tagged files as bounded repository context for the current task.",
        "Conversation continuity is supplied separately by ProtoLink's persistent per-agent history.",
        "The current user request appears at the end.",
    ]

    # Explicitly tagged files are highest priority, followed by automatically
    # selected repository evidence.
    if tagged_items:
        sections.extend(
            [
                "",
                "Tagged file context selected by the user with @. Treat it as read-only context unless the user asks for changes.",
            ]
        )
        for item in tagged_items:
            sections.extend(
                [
                    "",
                    f"--- @{item['path']} ({item['kind']}) ---",
                    item["content"],
                ]
            )

    if loom_prompt:
        sections.extend(["", loom_prompt])

    context = _bounded_text("\n".join(sections), _runtime_context_char_limit())
    return f"{context}\n\nCurrent user request:\n{prompt}"


def _runtime_context_char_limit() -> int:
    """Return one total application-context budget before the current request."""
    raw = os.getenv("PROTOAGENT_CONTEXT_CHARS")
    if raw:
        try:
            return max(1_000, int(raw))
        except ValueError:
            pass
    provider = str(visible_config().get("active_provider", "ollama"))
    return (
        LOCAL_RUNTIME_CONTEXT_CHARS if provider in LOCAL_PROVIDERS else REMOTE_RUNTIME_CONTEXT_CHARS
    )


def _bounded_text(value: str, limit: int) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)].rstrip() + "..."


def _prompt_with_tagged_context(prompt: str, tagged_context: dict[str, Any]) -> str:
    """Compatibility wrapper for callers that only provide tagged context."""
    return _runtime_prompt(prompt, tagged_context)


def _context_pack_for_prompt(
    prompt: str, workspace: str, tagged_context: dict[str, Any]
) -> dict[str, Any]:
    """Build Context Loom context without letting index failures break a run."""
    try:
        tagged_paths = [str(item.get("path", "")) for item in tagged_context.get("items", [])]
        return build_context_pack(prompt, workspace, tagged_paths=tagged_paths)
    except Exception as exc:
        return {
            "name": "Context Loom",
            "workspace": workspace,
            "query": prompt,
            "items": [],
            "errors": [f"Context Loom unavailable: {exc}"],
            "index": {"files_indexed": 0, "duration_ms": 0},
            "git": {"success": False, "status": []},
            "open_questions": [
                "Context Loom failed; Explorer should use direct read/search tools."
            ],
        }


def _context_summary(loom_context: dict[str, Any] | None) -> str:
    if not loom_context:
        return "none"
    errors = loom_context.get("errors", [])
    if errors:
        return "; ".join(str(error) for error in errors)
    return context_pack_summary(loom_context)


def _context_events(loom_context: dict[str, Any] | None) -> list[str]:
    if not loom_context:
        return []
    events = context_pack_events(loom_context)
    events.extend(f"Context Loom warning: {error}" for error in loom_context.get("errors", []))
    return events


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
