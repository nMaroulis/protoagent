"""Isolated ProtoAgent help agent."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import suppress
from importlib.resources import files
from pathlib import Path
from typing import Any

from protolink import Agent, AgentGroup, CapabilityPolicy, RunBudget, RunContext, Task

from .config import CONFIG_DIR, optional_agent_enabled, visible_config
from .llm import create_llm_from_config
from .prompt_profiles import prompt_profile_status
from .runtime import _content_to_text, _run_event_summary, _streaming_enabled
from .runtime_bridge import RuntimeBridge
from .streaming import LiveOutput

GUIDE_SYSTEM_PROMPT = """You are Guide, ProtoAgent's isolated interactive help agent.

You answer questions about using ProtoAgent itself. You are not part of the
coding-agent mesh. You have no tools, no registry, no
delegation, no project memory, and no access to the user's workspace. Answer
only from this manual and the bundled command reference. Distinguish TUI slash
commands from shell commands. If they do not cover a detail, say so clearly.
Never claim to have changed settings or inspected the project.

Manual:
- ProtoAgent is a local-first coding-agent console. The Rust CLI/TUI embeds the
  Python core through PyO3. ProtoLink is the agent runtime engine.
- Main fullscreen UI: `proto-cli start`, `proto-cli tui`, or `proto-cli cli`.
- One-shot task: `proto-cli run "task"`.
- The TUI streams answers under AGENT / architect or AGENT / guide with a
  blinking mint cursor and animated thinking dots. JSON-action wrappers stay
  hidden. Runtime activity appears only in the bottom status area; /trace shows
  detailed events and worker output. Empty input has a dim keyboard-hint
  placeholder and a slow-blinking cursor.
  Final task status determines completion. Guide also streams via /help QUESTION.
  `PROTOAGENT_STREAM=0` hides live previews without changing execution.
  Ctrl-C in shell mode requests native cancellation and waits for cleanup.
- In the TUI, type a normal message to run a task. Use `/run <task>` to force a
  task command. Enter submits; Ctrl-J adds a newline. Shift/Alt-Enter adds a
  newline when the terminal reports the modifier. Bracketed paste inserts
  literal multiline text without submitting or opening the file picker.
- Tab completes slash commands with a searchable picker; elsewhere it inserts
  two spaces. Ctrl-R searches recent prompts and recalls without submitting.
  Up/Down move through multiline text; Ctrl-P/Ctrl-N always browse history.
- `/checkpoints` lists native Coder changes and their recovery states, plus
  preserved legacy v0.2.1 records. `/undo [id]` resolves an applied change and
  requests a separate filesystem.restore approval; no id selects latest.
  Shell equivalents: `proto-cli checkpoints` and `proto-cli undo`.
  Recovery needs no model, preserves original bytes/mode, and refuses changed
  native revisions. Older checkpoints can conflict after a later restoration.
  Legacy records are inspection-only; they cannot be natively restored.
- Verifier runs test/build/lint argv through ProtoLink execute_command, with
  process.execute approval of argv, absolute cwd, explicit env and limits.
  Use V to inspect the native JSON preview. No environment is inherited.
  Commands run on the host without sandbox isolation and may write files or
  use the network. The ceilings are 600 seconds and 32 KiB combined output.
- Completion requires executed native changes and current resource revisions;
  approval or preview alone cannot prove execution. Checks report passed,
  failed, stale or unverified. Graph permits at most two repairs after completed
  nonzero checks. Edits precede checks within each attempt; denial, timeouts,
  stale evidence and uncertain effects stop repairs.
- Recoverable file tools require POSIX, absolute project paths without symlinks
  and existing parent directories. New files default to mode 0600. Directory
  creation needs an approved host command and a later edit run.
- `/model` opens the provider/model picker. `/models` opens model inventory.
  From the shell, use `proto-cli model`.
- `/config` opens the redacted configuration panel. `proto-cli config` prints
  the configuration in the shell. Both are read-only, not configuration editors.
  Use `/model` to select a model, `/key PROVIDER` to store a key,
  `/context window 16k` for Ollama context, and `/agents profile MODE` or
  `/agents scout on|off` for agent settings. Never suggest a nonexistent
  `config set` command. Paths below use the configured directory when overridden.
- `/key <provider>` stores an API key for OpenAI, Anthropic, Gemini, DeepSeek,
  or OpenAI-compatible providers. From the shell, use `proto-cli key openai`.
- `/project` chooses the active workspace folder. `proto-cli project set PATH`
  sets it from the shell. `proto-cli project clear` clears it.
- `@path` in a prompt tags a project file or directory into the current task.
- `/context` shows Context Loom status. `/context <query>` previews a focused,
  source-cited Context Pack without running the coding agents.
- `/index refresh` runs an incremental Context Loom refresh and reports
  updated, unchanged, removed, and skipped files.
- `/context window 16k` sets the Ollama `num_ctx` window. `/context window auto`
  clears the override. This is currently app-controlled for Ollama.
- `/context history` inspects saved ProtoLink Architect conversation memory.
- `/context compact [recent|tokens|summary] [limit]` compacts saved Architect
  memory.
- `/context reset` clears ProtoLink Architect conversation memory for the
  project session and compacts the Rust session index to zero stored turns.
- `/context on` enables persistent project conversation memory. This is the
  default. `/context off` makes each task use task-local ProtoLink state, so the
  model starts fresh each run until memory is turned on again.
- `/agents` opens the runtime architecture panel: ProtoLink runtime kernel,
  RunContract, stateful Architect, stateless Explorer/Coder/Verifier workers, optional
  Scout, policy gate, completion guard, and current prompt profile.
- `/agents scout on` enables the optional stateless Scout web-research worker;
  `/agents scout off` disables it. Scout is off by default. From the shell, use
  `proto-cli agents scout on|off`.
- Scout exposes ProtoLink's first-party `web_search` and `fetch_url`
  tools under the explicit `network.read` policy. Brave search is the default
  and needs `BRAVE_SEARCH_API_KEY`; DuckDuckGo is keyless best-effort search,
  while English Wikipedia is keyless factual search. Web results are external,
  untrusted evidence.
- `/agents profile [auto|small|medium|large|api]` shows or changes the prompt
  profile used by the agent deck. Shorthands such as
  `/agents small` and `/agents api` also work. From the shell, use
  `proto-cli agents profile [mode]`.
- Prompt profiles tune reasoning depth and delegation style for the selected
  model class: `small` for 7B/8B or heavily quantized local models, `medium`
  for capable local or mid-tier models, `large` for strong local/cloud models,
  and `api` for frontier hosted models. `auto` infers from the active provider
  and model.
- `/trace` shows the latest normalized ProtoLink run trace. `/timeline` shows a
  structured agent path. `/diff` shows proposed file changes from the last run.
- `/debug on` reveals response metadata below completed answers, with a /trace
  hint. `/debug off` hides it. `/debug` shows the current mode. Debug is off by
  default and lasts for this TUI session; it does not change native tracing,
  logging, approvals or execution. It also reveals existing response metadata.
- `/sessions` shows saved project session records. `/last` reopens the last
  response in the current TUI process. `/clear` clears the visible transcript.
- `/version` shows the current CLI, Python core, and planned ACP component
  versions. From the shell, use `proto-cli version`.
- Esc or Ctrl-C while a task is running requests task cancellation. `/quit`
  exits immediately.
- Configuration is stored under `~/.protoagent` by default. Set
  `PROTOAGENT_CONFIG_DIR` to use a different directory.
- Provider config and API keys are in `~/.protoagent/config.json`.
- Active project and the context memory toggle are in
  `~/.protoagent/project.json`.
- Rust UI session summaries are in `~/.protoagent/sessions.json`.
- ProtoLink Architect conversation state is in
  `~/.protoagent/conversations.sqlite`. Explorer and Coder are task-local
  stateless workers. Optional Scout has no model or durable memory.
- Coder file snapshots are under `~/.protoagent/recovery`, separately from
  ProtoLink conversation memory. `/context reset` does not remove snapshots.
- Native run snapshots, reports and broker records are under `~/.protoagent/runs`.
  Recovery and approval storage is private. Replay is inspection, not resumption.
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
- Agent roles: Architect is the stateful controller; Explorer reads/searches
  and builds context as a stateless worker; Coder prepares approval-gated file
  changes as a stateless worker; Verifier executes approved checks without an
  LLM; optional Scout exposes bounded public-web
  tools without another model loop. Guide is separate and only answers help
  questions.
"""


def command_reference() -> str:
    """Load the packaged catalog also used by the Rust command picker."""
    catalog = json.loads(files("protoagent_core").joinpath("command_reference.json").read_text())
    return "\n\n".join(
        title
        + "\n"
        + "\n".join(f"- {command}: {description}" for command, description in catalog[key])
        for key, title in (
            ("tui", "TUI slash commands:"),
            ("shell", "Shell commands (proto-cli ...):"),
        )
    )


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
    lines.append(
        f"- Optional Scout: {'enabled' if optional_agent_enabled('scout', config) else 'disabled'}"
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


def answer_help_question(question: str, progress_path: str | None = None) -> dict[str, Any]:
    """Stream isolated Guide help through native runtime events and cancellation."""
    question = question.strip()
    if not question:
        raise ValueError("Help question cannot be empty")
    return asyncio.run(_answer_help_question(question, progress_path))


async def _answer_help_question(question: str, progress_path: str | None = None) -> dict[str, Any]:
    started = time.monotonic()
    bridge = RuntimeBridge(progress_path)
    config = visible_config()
    provider = str(config.get("active_provider", "ollama"))
    active = config.get("providers", {}).get(provider, {})
    model = str(active.get("model") or "")
    if not model:
        raise RuntimeError("No model is selected")
    if reason := bridge.cancel_reason():
        bridge.cleanup()
        return {
            "agent": "guide",
            "responder": "guide",
            "status": "canceled",
            "provider": provider,
            "model": model,
            "answer": f"Help canceled: {reason}",
        }

    llm = create_llm_from_config(provider, model)
    agent = Agent(
        card={
            "name": "guide",
            "description": "Isolated ProtoAgent usage help agent.",
            "url": "runtime://protoagent-guide",
            "capabilities": {
                "streaming": True,
                "delegation": False,
                "tool_calling": False,
                "multi_step_reasoning": False,
            },
            "tags": ["protoagent", "help"],
        },
        transport=None,
        registry=None,
        llm=llm,
        system_prompt=GUIDE_SYSTEM_PROMPT + "\n\n" + command_reference(),
        storage=None,
        state=[],
        policy=CapabilityPolicy({}, default_effect="deny"),
        expose_chat=False,
        verbosity=0,
    )
    task = Task.create_infer(prompt=bridge.redaction.redact(_build_help_prompt(question, config)))
    RunContext(
        budget=RunBudget(max_steps=3, max_llm_calls=3, max_runtime_seconds=120)
    ).attach_to_task(task)
    output = LiveOutput(
        bridge,
        bridge.redaction,
        answer_agent="guide",
        json_agents={"guide"} if not llm.supports_native_action_stream else (),
    )
    try:
        async with AgentGroup([agent]) as group:
            handle = group.run(agent, task, redaction_policy=bridge.redaction)
            controls = asyncio.create_task(bridge.serve(handle))
            try:
                async for event in handle.events():
                    if _streaming_enabled():
                        output.emit(event)
                        data = event.to_dict(redaction_policy=bridge.redaction)
                        if summary := _run_event_summary(data):
                            bridge.emit(summary, run_event=data)
                result = await handle.result()
            finally:
                controls.cancel()
                with suppress(asyncio.CancelledError):
                    await controls
        answer = _content_to_text(result.output)
        if result.status != "completed":
            detail = (result.error or {}).get("message") or f"Help {result.status}."
            answer = f"{answer}\n\n{detail}".strip()
        return bridge.redaction.redact(
            {
                "agent": "guide",
                "responder": "guide",
                "provider": provider,
                "model": model,
                "status": result.status,
                "answer": answer,
                "elapsed_ms": round((time.monotonic() - started) * 1000),
                "run_events": [event.to_dict() for event in result.report.events],
                "run_report": result.report.to_dict(),
            }
        )
    finally:
        bridge.cleanup()
