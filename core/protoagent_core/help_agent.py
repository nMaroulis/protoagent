"""Isolated ProtoAgent help agent."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from protolink import Agent, CapabilityPolicy, Task

from .config import CONFIG_DIR, visible_config
from .llm import create_llm_from_config
from .prompt_profiles import prompt_profile_status

GUIDE_SYSTEM_PROMPT = """You are Guide, ProtoAgent's isolated interactive help agent.

You answer questions about using ProtoAgent itself. You are not part of the
Architect/Explorer/Coder coding mesh. You have no tools, no registry, no
delegation, no project memory, and no access to the user's workspace. Answer
only from this manual. If the manual does not cover a detail, say so clearly.

Return exactly one JSON object:
{"type":"final","content":"your concise answer"}

Manual:
- ProtoAgent is a local-first coding-agent console. The Rust CLI/TUI embeds the
  Python core through PyO3. ProtoLink is the agent runtime engine.
- Main fullscreen UI: `proto-cli start`, `proto-cli tui`, or `proto-cli cli`.
- One-shot task: `proto-cli run "task"`.
- In the TUI, type a normal message to run a task. Use `/run <task>` to force a
  task command.
- `/model` opens the provider/model picker. `/models` opens model inventory.
  From the shell, use `proto-cli model`.
- `/key <provider>` stores an API key for OpenAI, Anthropic, Gemini, DeepSeek,
  or OpenAI-compatible providers. From the shell, use `proto-cli key openai`.
- `/project` chooses the active workspace folder. `proto-cli project set PATH`
  sets it from the shell. `proto-cli project clear` clears it.
- `@path` in a prompt tags a project file or directory into the current task.
- `/context` shows Context Loom status. `/context <query>` previews a focused,
  source-cited Context Pack without running the coding agents.
- `/index refresh` rebuilds the Context Loom index.
- `/context window 16k` sets the Ollama `num_ctx` window. `/context window auto`
  clears the override. This is currently app-controlled for Ollama.
- `/context history` inspects saved ProtoLink conversation memory.
- `/context compact [recent|tokens|summary] [limit]` compacts saved memory.
- `/context reset` clears ProtoLink conversation memory for the project session
  and compacts the Rust session index to zero stored turns.
- `/context on` enables persistent project conversation memory. This is the
  default. `/context off` makes each task use task-local ProtoLink state, so the
  model starts fresh each run until memory is turned on again.
- `/agents` opens the Architect / Explorer / Coder panel and shows the current
  prompt profile.
- `/agents profile [auto|small|medium|large|api]` shows or changes the prompt
  profile used by Architect, Explorer, and Coder. Shorthands such as
  `/agents small` and `/agents api` also work. From the shell, use
  `proto-cli agents profile [mode]`.
- Prompt profiles tune reasoning depth and delegation style for the selected
  model class: `small` for 7B/8B or heavily quantized local models, `medium`
  for capable local or mid-tier models, `large` for strong local/cloud models,
  and `api` for frontier hosted models. `auto` infers from the active provider
  and model.
- `/trace` shows the latest normalized ProtoLink run trace. `/timeline` shows a
  structured agent path. `/diff` shows proposed file changes from the last run.
- `/sessions` shows saved project session records. `/last` reopens the last
  response in the current TUI process. `/clear` clears the visible transcript.
- Esc or Ctrl-C while a task is running requests task cancellation. `/quit`
  exits immediately.
- Configuration is stored under `~/.protoagent` by default. Set
  `PROTOAGENT_CONFIG_DIR` to use a different directory.
- Provider config and API keys are in `~/.protoagent/config.json`.
- Active project and the context memory toggle are in
  `~/.protoagent/project.json`.
- Rust UI session summaries are in `~/.protoagent/sessions.json`.
- ProtoLink per-agent conversation state is in
  `~/.protoagent/conversations.sqlite`.
- Context Loom indexes are under `~/.protoagent/indexes`.
- Short-lived live progress/control JSONL files are written in the OS temp
  directory while a task is running and are cleaned up after the run.
- Durable local ProtoLink telemetry is written to `~/.protoagent/traces.jsonl`
  only when `PROTOAGENT_TRACE=1` is enabled.
- Useful runtime environment switches: `PROTOAGENT_STREAM=0`,
  `PROTOAGENT_AGENT_TRANSPORT=http`, `PROTOAGENT_STREAM_TRACE_LIMIT=120`,
  `PROTOAGENT_RUN_MAX_STEPS`, `PROTOAGENT_RUN_MAX_LLM_CALLS`,
  `PROTOAGENT_RUN_MAX_TOOL_CALLS`, `PROTOAGENT_RUN_MAX_SECONDS`,
  `PROTOAGENT_RUN_MAX_INPUT_TOKENS`, `PROTOAGENT_RUN_MAX_OUTPUT_TOKENS`,
  `PROTOAGENT_CONTEXT_CHARS`, and `PROTOAGENT_OLLAMA_NUM_CTX`.
- Agent roles: Architect receives the user request and routes work; Explorer
  reads/searches/builds context; Coder prepares approval-gated file changes.
  Guide is separate and only answers help questions.
"""


def _build_help_prompt(question: str, config: dict[str, Any]) -> str:
    settings = _settings_context(config)
    return (
        "Use this per-call settings snapshot only when the user asks about the current "
        "ProtoAgent setup. It is not conversation memory.\n\n"
        f"{settings}\n\n"
        f"User help question:\n{question}"
    )


def _settings_context(config: dict[str, Any]) -> str:
    provider = str(config.get("active_provider") or "not selected")
    active = config.get("providers", {}).get(provider, {})
    if not isinstance(active, dict):
        active = {}

    model = str(active.get("model") or "not selected")
    lines = [
        "Current ProtoAgent settings (redacted):",
        f"- Active provider: {provider}",
        f"- Active model: {model}",
    ]
    profile = prompt_profile_status(config, provider=provider, model=model)
    profile_label = str(profile.get("label") or profile.get("resolved") or "unknown")
    lines.append(
        "- Prompt profile: "
        f"{profile.get('configured', 'auto')} configured, "
        f"{profile.get('resolved', 'auto')} resolved ({profile_label})"
    )
    label = str(active.get("label") or "")
    if label and label != provider:
        lines.append(f"- Provider label: {label}")
    base_url = str(active.get("base_url") or "")
    if base_url:
        lines.append(f"- Provider base URL: {base_url}")
    context_window = active.get("context_window")
    if context_window:
        lines.append(f"- Context window override: {context_window} tokens")

    key_status = "set" if active.get("api_key_set") else "not set"
    key_source = "environment" if active.get("from_env") else "config"
    if active.get("api_key_set"):
        lines.append(f"- API key: {key_status} from {key_source}")
    else:
        lines.append(f"- API key: {key_status}")

    config_path = str(config.get("config_path") or "")
    if config_path:
        lines.append(f"- Provider config path: {config_path}")

    project_settings = _project_settings(config)
    lines.append(f"- Active project: {project_settings['active_project']}")
    lines.append(f"- Persistent context memory: {project_settings['context_memory']}")
    lines.append(f"- Project config path: {project_settings['path']}")
    return "\n".join(lines)


def _project_settings(config: dict[str, Any]) -> dict[str, str]:
    path = _project_config_path(config)
    data: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            data = {}

    enabled = data.get("context_memory_enabled")
    if enabled is None:
        memory = "on (default)"
    else:
        memory = "on" if bool(enabled) else "off"
    return {
        "active_project": str(data.get("active_project") or "not selected"),
        "context_memory": memory,
        "path": str(path),
    }


def _project_config_path(config: dict[str, Any]) -> Path:
    config_path = str(config.get("config_path") or "")
    if config_path:
        return Path(config_path).expanduser().parent / "project.json"
    return CONFIG_DIR / "project.json"


def answer_help_question(question: str) -> dict[str, Any]:
    """Answer a ProtoAgent usage question with the isolated Guide agent."""
    question = question.strip()
    if not question:
        raise ValueError("Help question cannot be empty")
    return asyncio.run(_answer_help_question(question))


async def _answer_help_question(question: str) -> dict[str, Any]:
    config = visible_config()
    provider = str(config.get("active_provider", "ollama"))
    active = config.get("providers", {}).get(provider, {})
    model = str(active.get("model") or "")
    if not model:
        raise RuntimeError("No model is selected")

    agent = Agent(
        card={
            "name": "guide",
            "description": "Isolated ProtoAgent usage help agent.",
            "url": "runtime://protoagent-guide",
            "capabilities": {
                "delegation": False,
                "tool_calling": False,
                "multi_step_reasoning": False,
            },
            "tags": ["protoagent", "help"],
        },
        transport=None,
        registry=None,
        llm=create_llm_from_config(provider, model),
        system_prompt=GUIDE_SYSTEM_PROMPT,
        storage=None,
        state=[],
        policy=CapabilityPolicy({}, default_effect="deny"),
        override_system_prompt=True,
        verbosity=0,
    )
    task = Task.create_infer(prompt=_build_help_prompt(question, config))
    result = await agent.handle_task(task)
    return {
        "agent": "guide",
        "provider": provider,
        "model": model,
        "answer": _task_last_part_content(result) or "",
    }


def _task_last_part_content(task: Any) -> str:
    content = task.get_last_part_content() if hasattr(task, "get_last_part_content") else None
    if isinstance(content, dict):
        for key in ("content", "text", "answer"):
            value = content.get(key)
            if value:
                return str(value)
        return str(content)
    return str(content or "")
