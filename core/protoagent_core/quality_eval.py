"""Prompt-profile quality evaluation harness for ProtoAgent."""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from .config import load_config, set_agent_prompt_profile, visible_config
from .prompt_profiles import RESOLVED_PROMPT_PROFILES, normalize_prompt_profile
from .tools import workspace_root

EVAL_VERSION = 1
DEFAULT_PROFILES = ("small", "medium", "large", "api")
EVAL_MODES = ("plan", "scaffold", "live")


@dataclass(frozen=True)
class EvalTask:
    """One reusable prompt-profile benchmark task."""

    id: str
    category: str
    prompt: str
    expected_paths: tuple[str, ...]
    requires_explorer: bool = True
    requires_coder: bool = False
    requires_docs: bool = False
    requires_tests: bool = False
    max_changed_files: int = 4

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable task contract."""
        data = asdict(self)
        data["expected_paths"] = list(self.expected_paths)
        return data


EVAL_TASKS: tuple[EvalTask, ...] = (
    EvalTask(
        id="explain-cancellation-path",
        category="read-only-runtime",
        prompt=(
            "Explain how Esc or Ctrl-C cancellation travels from the TUI into "
            "ProtoLink task cancellation. Cite the key files."
        ),
        expected_paths=(
            "cli/src/terminal_ui.rs",
            "cli/src/progress.rs",
            "core/protoagent_core/runtime.py",
            "core/protoagent_core/runtime_bridge.py",
        ),
        requires_coder=False,
    ),
    EvalTask(
        id="debug-trace-docs",
        category="docs-change",
        prompt=(
            "Improve the docs so a user can copy-paste a failing-run debug trace "
            "command and know where the JSONL trace file lives."
        ),
        expected_paths=(
            "docs/content/cli/safety-tracing.md",
            "docs/content/reference/troubleshooting.md",
            "docs/content/reference/environment.md",
        ),
        requires_coder=True,
        requires_docs=True,
        max_changed_files=3,
    ),
    EvalTask(
        id="prompt-profile-command-docs",
        category="docs-change",
        prompt=(
            "Update the command docs to describe `/agents profile "
            "auto|small|medium|large|api` and the matching shell command."
        ),
        expected_paths=("docs/content/cli/commands.md", "docs/content/cli/models-and-config.md"),
        requires_coder=True,
        requires_docs=True,
        max_changed_files=2,
    ),
    EvalTask(
        id="config-import-hygiene",
        category="architecture-review",
        prompt=(
            "Find whether lightweight config commands import the full ProtoLink "
            "agent stack, and propose the smallest safe boundary if they do."
        ),
        expected_paths=(
            "core/protoagent_core/agent_engine.py",
            "core/protoagent_core/config.py",
            "core/protoagent_core/agents/__init__.py",
        ),
        requires_coder=False,
    ),
    EvalTask(
        id="context-window-parser-test",
        category="test-change",
        prompt=(
            "Add a focused regression test proving the context-window parser "
            "accepts 32k and rejects malformed values."
        ),
        expected_paths=("core/tests/test_llm_context.py", "core/protoagent_core/agent_engine.py"),
        requires_coder=True,
        requires_tests=True,
        max_changed_files=2,
    ),
    EvalTask(
        id="provider-config-map",
        category="read-only-config",
        prompt=(
            "Identify the files that own provider discovery, visible config, "
            "and LLM construction. Answer with paths and responsibilities."
        ),
        expected_paths=(
            "core/protoagent_core/models.py",
            "core/protoagent_core/config.py",
            "core/protoagent_core/llm.py",
        ),
        requires_coder=False,
    ),
    EvalTask(
        id="runtime-budget-doc-note",
        category="docs-change",
        prompt=(
            "Add a concise docs note explaining which environment variables "
            "populate ProtoLink RunBudget for a task."
        ),
        expected_paths=(
            "core/README.md",
            "docs/content/core/runtime.md",
            "docs/content/reference/environment.md",
        ),
        requires_coder=True,
        requires_docs=True,
        max_changed_files=3,
    ),
    EvalTask(
        id="approval-denial-regression",
        category="test-change",
        prompt=(
            "Add or strengthen a regression test proving a denied Coder "
            "workspace.write approval leaves the target file unchanged."
        ),
        expected_paths=("core/tests/test_runtime_integration.py",),
        requires_coder=True,
        requires_tests=True,
        max_changed_files=1,
    ),
    EvalTask(
        id="tui-agents-panel-copy",
        category="cli-change",
        prompt=(
            "Improve the TUI Agents panel copy so it mentions the current prompt "
            "profile and how to change it."
        ),
        expected_paths=("cli/src/terminal_ui/render.rs", "cli/src/terminal_ui/state.rs"),
        requires_coder=True,
        max_changed_files=2,
    ),
    EvalTask(
        id="context-loom-format-map",
        category="read-only-context",
        prompt=(
            "Use repository evidence to explain where Context Loom prompt "
            "formatting lives and how it is injected before the model run."
        ),
        expected_paths=(
            "core/protoagent_core/context/packer.py",
            "core/protoagent_core/agent_engine.py",
            "docs/content/core/context-loom.md",
        ),
        requires_coder=False,
    ),
    EvalTask(
        id="guide-profile-help",
        category="help-doc-change",
        prompt=(
            "Update Guide or help documentation so users can ask how prompt "
            "profiles work and how to switch them."
        ),
        expected_paths=(
            "core/protoagent_core/help_agent.py",
            "docs/content/cli/commands.md",
            "docs/content/core/agents.md",
        ),
        requires_coder=True,
        requires_docs=True,
        max_changed_files=3,
    ),
    EvalTask(
        id="state-memory-boundary",
        category="read-only-memory",
        prompt=(
            "Explain the boundary between Rust session history and ProtoLink "
            "model-facing conversation memory. Cite source and docs files."
        ),
        expected_paths=(
            "cli/src/sessions.rs",
            "core/protoagent_core/history.py",
            "docs/content/core/state-memory.md",
        ),
        requires_coder=False,
    ),
)


def run_quality_eval(
    *,
    workspace: str | None = None,
    profiles: Iterable[str] | str | None = None,
    task_ids: Iterable[str] | str | None = None,
    mode: str = "scaffold",
    limit: int | None = None,
) -> dict[str, Any]:
    """Run the prompt-profile quality benchmark.

    ``plan`` only returns the benchmark matrix. ``scaffold`` runs the full
    prompt/context plumbing without contacting a model. ``live`` calls the
    selected model with approvals auto-denied because no interactive progress
    bridge is passed to the runtime.
    """
    mode = _normalize_mode(mode)
    selected_profiles = _normalize_profiles(profiles)
    selected_tasks = _select_tasks(task_ids, limit=limit)
    project = str(workspace_root(workspace))
    started = time.time()
    config_before = load_config()
    try:
        original_profile = normalize_prompt_profile(str(config_before.get("agent_prompt_profile", "auto")))
    except ValueError:
        original_profile = "auto"
    profile_results: list[dict[str, Any]] = []

    try:
        for profile in selected_profiles:
            set_agent_prompt_profile(profile)
            profile_status = visible_config().get("agent_prompt_profile", profile)
            task_results = [
                _run_task(profile, profile_status, task, project, mode) for task in selected_tasks
            ]
            profile_results.append(_profile_report(profile, task_results))
    finally:
        set_agent_prompt_profile(original_profile)

    summary = _summary(profile_results, selected_profiles, selected_tasks, mode)
    return {
        "version": EVAL_VERSION,
        "mode": mode,
        "workspace": project,
        "elapsed_ms": int((time.time() - started) * 1000),
        "summary": summary,
        "profiles": profile_results,
        "tasks": [task.to_dict() for task in selected_tasks],
        "notes": _mode_notes(mode),
    }


def list_eval_tasks() -> dict[str, Any]:
    """Return the built-in prompt-profile benchmark task set."""
    return {
        "version": EVAL_VERSION,
        "profiles": list(DEFAULT_PROFILES),
        "modes": list(EVAL_MODES),
        "tasks": [task.to_dict() for task in EVAL_TASKS],
    }


def score_response(response: dict[str, Any], task: EvalTask, *, mode: str = "live") -> dict[str, Any]:
    """Score one model response against one benchmark task contract."""
    observations = _observations(response, task)
    checks = [
        _check(
            "completed",
            str(response.get("status") or "") in {"answered", "ready"},
            "Run returned a terminal answer or scaffold diagnostic.",
        ),
        _check(
            "expected_path_hit",
            observations["expected_path_hit"],
            "Output, trace, target, or approval references an expected source path.",
        ),
        _check(
            "explorer_usage",
            observations["used_explorer"] if task.requires_explorer else not observations["used_explorer"],
            (
                "Explorer was used for repository evidence."
                if task.requires_explorer
                else "Explorer was not used for a direct task."
            ),
        ),
        _check(
            "coder_usage",
            observations["used_coder"] if task.requires_coder else not observations["used_coder"],
            (
                "Coder was used for the requested change."
                if task.requires_coder
                else "Coder was not used for a read-only task."
            ),
        ),
        _check(
            "docs_touched",
            observations["docs_touched"] if task.requires_docs else True,
            "Docs were included when the task asked for user-facing docs.",
        ),
        _check(
            "tests_touched",
            observations["tests_touched"] if task.requires_tests else True,
            "Tests were included when the task asked for regression coverage.",
        ),
        _check(
            "over_edit_guard",
            not observations["over_edit_risk"],
            f"Touched path count stays within the task cap of {task.max_changed_files}.",
        ),
    ]
    if mode == "scaffold":
        for check in checks:
            if check["id"] in {"explorer_usage", "coder_usage", "docs_touched", "tests_touched"}:
                check["informational"] = True

    scored_checks = [check for check in checks if not check.get("informational")]
    points = sum(1 for check in scored_checks if check["passed"])
    possible = len(scored_checks)
    return {
        "points": points,
        "possible": possible,
        "score": round(points / possible, 3) if possible else None,
        "checks": checks,
        "observations": observations,
    }


def _run_task(
    profile: str,
    profile_status: Any,
    task: EvalTask,
    workspace: str,
    mode: str,
) -> dict[str, Any]:
    if mode == "plan":
        return {
            "task_id": task.id,
            "profile": profile,
            "profile_status": profile_status,
            "mode": mode,
            "prompt": task.prompt,
            "score": None,
            "response": None,
            "error": "",
        }

    from .agent_engine import process_prompt

    previous_scaffold = os.environ.get("PROTOAGENT_SCAFFOLD")
    if mode == "scaffold":
        os.environ["PROTOAGENT_SCAFFOLD"] = "1"
    else:
        os.environ.pop("PROTOAGENT_SCAFFOLD", None)

    try:
        response = json.loads(process_prompt(task.prompt, workspace, None, None))
        score = score_response(response, task, mode=mode)
        return {
            "task_id": task.id,
            "profile": profile,
            "profile_status": profile_status,
            "mode": mode,
            "prompt": task.prompt,
            "score": score,
            "response": _response_digest(response),
            "error": "",
        }
    except Exception as exc:  # pragma: no cover - live provider failures are environment-specific.
        return {
            "task_id": task.id,
            "profile": profile,
            "profile_status": profile_status,
            "mode": mode,
            "prompt": task.prompt,
            "score": _error_score(str(exc)),
            "response": None,
            "error": str(exc),
        }
    finally:
        if previous_scaffold is None:
            os.environ.pop("PROTOAGENT_SCAFFOLD", None)
        else:
            os.environ["PROTOAGENT_SCAFFOLD"] = previous_scaffold


def _response_digest(response: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": str(response.get("status") or ""),
        "headline": str(response.get("headline") or ""),
        "provider": str(response.get("provider") or ""),
        "model": str(response.get("model") or ""),
        "responder": str(response.get("responder") or ""),
        "warning": str(response.get("warning") or ""),
        "elapsed_ms": int(response.get("elapsed_ms") or 0),
        "answer_preview": _preview(response.get("answer")),
        "target": str(response.get("file_target") or ""),
        "run_event_count": len(response.get("run_events") or []),
        "event_count": len(response.get("events") or []),
        "approval_count": len(response.get("approval_requests") or []),
        "diff_present": bool(str(response.get("diff") or "").strip()),
    }


def _observations(response: dict[str, Any], task: EvalTask) -> dict[str, Any]:
    text = _response_text(response)
    paths = _touched_paths(response)
    run_events = response.get("run_events") if isinstance(response.get("run_events"), list) else []
    return {
        "used_explorer": _used_agent(run_events, "explorer"),
        "used_coder": _used_agent(run_events, "coder")
        or bool(response.get("approval_requests"))
        or bool(str(response.get("diff") or "").strip()),
        "approval_requested": bool(response.get("approval_requests")),
        "diff_present": bool(str(response.get("diff") or "").strip()),
        "docs_touched": any(_is_docs_path(path) for path in paths),
        "tests_touched": any(_is_test_path(path) for path in paths),
        "expected_path_hit": any(path.lower() in text for path in task.expected_paths),
        "touched_paths": sorted(paths),
        "touched_path_count": len(paths),
        "over_edit_risk": len(paths) > task.max_changed_files,
    }


def _used_agent(run_events: list[Any], agent_name: str) -> bool:
    agent_name = agent_name.lower()
    for event in run_events:
        if not isinstance(event, dict):
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        llm_type = str(payload.get("llm_event_type") or "").lower()
        if llm_type == "agent_call_start" and str(metadata.get("agent") or "").lower() == agent_name:
            return True
        if str(event.get("agent_name") or "").lower() == agent_name and llm_type in {
            "tool_start",
            "llm_step",
            "llm_final",
        }:
            return True
    return False


def _touched_paths(response: dict[str, Any]) -> set[str]:
    paths: set[str] = set()
    target = str(response.get("file_target") or "")
    for part in target.split(","):
        cleaned = part.strip()
        if cleaned:
            paths.add(cleaned)
    for request in response.get("approval_requests") or []:
        if not isinstance(request, dict):
            continue
        action = request.get("action") if isinstance(request.get("action"), dict) else {}
        metadata = action.get("metadata") if isinstance(action.get("metadata"), dict) else {}
        payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
        arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
        path = str(metadata.get("path") or arguments.get("path") or "").strip()
        if path:
            paths.add(path)
    return paths


def _response_text(response: dict[str, Any]) -> str:
    pieces = [
        response.get("answer"),
        response.get("thought_process"),
        response.get("file_target"),
        response.get("diff"),
        response.get("events"),
        response.get("run_events"),
        response.get("approval_requests"),
    ]
    return " ".join(json.dumps(piece, ensure_ascii=True).lower() for piece in pieces if piece)


def _profile_report(profile: str, task_results: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [
        result["score"]
        for result in task_results
        if isinstance(result.get("score"), dict) and result["score"].get("score") is not None
    ]
    points = sum(int(score.get("points") or 0) for score in scored)
    possible = sum(int(score.get("possible") or 0) for score in scored)
    return {
        "profile": profile,
        "points": points,
        "possible": possible,
        "score": round(points / possible, 3) if possible else None,
        "tasks": task_results,
    }


def _summary(
    profile_results: list[dict[str, Any]],
    profiles: tuple[str, ...],
    tasks: list[EvalTask],
    mode: str,
) -> dict[str, Any]:
    points = sum(int(profile.get("points") or 0) for profile in profile_results)
    possible = sum(int(profile.get("possible") or 0) for profile in profile_results)
    return {
        "profile_count": len(profiles),
        "task_count": len(tasks),
        "run_count": len(profiles) * len(tasks),
        "points": points,
        "possible": possible,
        "score": round(points / possible, 3) if possible else None,
        "mode": mode,
    }


def _check(check_id: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"id": check_id, "passed": bool(passed), "detail": detail}


def _error_score(error: str) -> dict[str, Any]:
    return {
        "points": 0,
        "possible": 1,
        "score": 0.0,
        "checks": [_check("run_error", False, error)],
        "observations": {},
    }


def _normalize_mode(mode: str) -> str:
    normalized = (mode or "scaffold").strip().lower()
    if normalized not in EVAL_MODES:
        raise ValueError(f"Eval mode must be one of: {', '.join(EVAL_MODES)}")
    return normalized


def _normalize_profiles(profiles: Iterable[str] | str | None) -> tuple[str, ...]:
    values = _split_values(profiles) or list(DEFAULT_PROFILES)
    normalized = tuple(normalize_prompt_profile(value, allow_auto=False) for value in values)
    invalid = [value for value in normalized if value not in RESOLVED_PROMPT_PROFILES]
    if invalid:
        raise ValueError(f"Eval profiles must be one of: {', '.join(RESOLVED_PROMPT_PROFILES)}")
    return normalized


def _select_tasks(task_ids: Iterable[str] | str | None, *, limit: int | None) -> list[EvalTask]:
    ids = _split_values(task_ids)
    tasks = list(EVAL_TASKS)
    if ids:
        by_id = {task.id: task for task in tasks}
        missing = [task_id for task_id in ids if task_id not in by_id]
        if missing:
            raise ValueError(f"Unknown eval task(s): {', '.join(missing)}")
        tasks = [by_id[task_id] for task_id in ids]
    if limit is not None:
        if limit < 1:
            raise ValueError("Eval limit must be at least 1")
        tasks = tasks[:limit]
    return tasks


def _split_values(values: Iterable[str] | str | None) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        raw = values.split(",")
    else:
        raw = []
        for value in values:
            raw.extend(str(value).split(","))
    return [value.strip() for value in raw if value.strip()]


def _is_docs_path(path: str) -> bool:
    return path.startswith("docs/") or path in {"README.md", "core/README.md", "cli/README.md"}


def _is_test_path(path: str) -> bool:
    name = Path(path).name.lower()
    return "test" in name or "/tests/" in path or path.startswith("core/tests/")


def _preview(value: Any, *, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


def _mode_notes(mode: str) -> list[str]:
    if mode == "plan":
        return ["Plan mode lists benchmark tasks without running the core."]
    if mode == "scaffold":
        return [
            "Scaffold mode sets PROTOAGENT_SCAFFOLD=1 and never contacts a model.",
            "Behavioral checks are informational because no real agent delegation occurs.",
        ]
    return [
        "Live mode calls the selected model for each profile/task.",
        "Workspace writes are not executed: without an interactive progress bridge, approvals are auto-denied.",
    ]
