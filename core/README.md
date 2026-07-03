# ProtoAgent Core

Python brain for the ProtoAgent frontends. The Rust CLI imports this package
through PyO3 and expects JSON strings from `protoagent_core.agent_engine`.

Current package version: `0.1.0`. The source of truth is
`core/pyproject.toml`, mirrored by `protoagent_core.__version__`.

Install ProtoLink 0.6.3 or newer with the HTTP/SSE transport and LLM extras so
the embedded Agent runtime can import streaming agents, lifecycle-aware task
status events, recursive stream serialization, history compaction, metrics,
state operations, run reports, context manifests, and provider clients:

```bash
pip install "protolink[http,llms]>=0.6.3"
```

## Layout

- `protoagent_core/agent_engine.py` - PyO3-facing functions for prompts, model discovery, config, and doctor checks.
- `protoagent_core/_version.py` - Runtime version metadata and component version inventory for the CLI.
- `protoagent_core/runtime.py` - Embedded ProtoLink mesh runner. It attaches `RunContext`/`RunBudget` plus a task `RunContract`, records `RunEvent`s with `RunRecorder`, validates completion, can write local ProtoLink traces, and sends tasks to Architect with `AgentClient`.
- `protoagent_core/history.py` - ProtoLink state-operation facade for automatic Architect token-budget compaction plus explicit history/compact/reset commands.
- `protoagent_core/runtime_bridge.py` - Application approval and cancellation bridge for the Rust CLI.
- `protoagent_core/help_agent.py` - Isolated Guide agent for `/help <question>` usage help; it is not registered with the coding mesh and has no tools, delegation, storage, or project session.
- `protoagent_core/models.py` - Ollama, LM Studio, OpenAI-compatible, llama.cpp, and API model inventory.
- `protoagent_core/config.py` - Provider config and API-key storage at `~/.protoagent/config.json`.
- `protoagent_core/prompt_profiles.py` - Small/medium/large/API prompt profiles for the agent deck.
- `protoagent_core/quality_eval.py` - Fixed prompt-profile benchmark tasks and scoring helpers.
- `protoagent_core/context/` - Context Loom indexer, SQLite store, and source-cited Context Pack builder.
- `protoagent_core/agents/` - ProtoLink Architect, Explorer, and Coder factories. Architect is the stateful controller; Explorer and Coder are task-local stateless workers.
- `protoagent_core/run_contracts.py` - Runtime task classification and completion validation for required workers, approval requests, and diff artifacts.
- `protoagent_core/tools.py` - Workspace-safe exploration, diff preview, and authorized write helpers.

## Provider Execution

The CLI invokes the selected provider/model through ProtoLink agents by
default. The selected model is used to create fresh LLM instances for the
Architect, Explorer, and Coder on each run. Agents use ProtoLink's SSE
JSON-RPC lifecycle-aware task stream by default, while the Registry remains on
plain HTTP.

Each LLM is configured through `LLM.configure_metrics(LLMModelProfile(...))`.
For Ollama, the same selected window is sent as `num_ctx` and recorded in the
profile, so ProtoLink's `context.prepared`, `llm_context`, and
`llm_call_metrics` events drive the terminal context meter. Conversation
continuity lives in ProtoLink's Architect SQLite state. Explorer and Coder use
task-local in-memory state, so their worker calls do not accumulate durable
conversation history. Before a session resumes, the core uses
`Agent.compact_state(strategy="tokens")` when the Architect history budget is
exceeded; `/context history`, `/context compact`, and `/context reset` use
ProtoLink state operation reports. `/context on` and `/context off` control
whether Rust passes the stable project session ID or a task-local session to
ProtoLink. The Rust trace and timeline views consume normalized `RunEvent`s
first, including causal IDs for nested routes, and each run returns a redacted
ProtoLink `RunReport` for diagnostics and replay.

Before each model run, Context Loom refreshes a deterministic local index and
injects a bounded Context Pack into the Architect prompt. Explorer also exposes
`build_context_pack` as a ProtoLink tool so the agent mesh can ask for a
focused evidence pack during a run.

Agent prompts are tuned through a configurable prompt profile:
`auto`, `small`, `medium`, `large`, or `api`. `auto` resolves from the active
provider/model. The profile changes only the role instructions for Architect,
Explorer, and Coder; ProtoLink still owns delegation, tools, memory, policies,
runtime events, and reports. A `RunContract` is inferred before the model runs
and later checked against worker usage, approval requests, and diff artifacts.
Write tasks that finish without Coder, approval/diff artifacts, or an explicit
blocker are returned as `incomplete`.

Prompt profile quality can be checked with the built-in eval harness:
`proto-cli eval profiles` runs a scaffold smoke without contacting a model,
while `proto-cli eval profiles --live` runs the selected model with write
approvals auto-denied.

Useful runtime switches:

- `PROTOAGENT_STREAM=0` disables stream consumption and uses request/response.
- `PROTOAGENT_AGENT_TRANSPORT=http` forces the older HTTP-only agent mesh.
- `PROTOAGENT_STREAM_TRACE_LIMIT=120` controls how many stream summaries are retained for the Rust UI.
- `PROTOAGENT_TRACE=1` enables `LocalTraceTelemetry` JSONL traces at `~/.protoagent/traces.jsonl`.
- `PROTOAGENT_RUN_MAX_STEPS`, `PROTOAGENT_RUN_MAX_LLM_CALLS`, `PROTOAGENT_RUN_MAX_TOOL_CALLS`, `PROTOAGENT_RUN_MAX_SECONDS`, `PROTOAGENT_RUN_MAX_INPUT_TOKENS`, and `PROTOAGENT_RUN_MAX_OUTPUT_TOKENS` populate the run's typed `RunBudget`.

Use scaffold mode only when you want to test the Rust/Python contract without contacting a model:

```bash
PROTOAGENT_SCAFFOLD=1 cargo run --manifest-path cli/Cargo.toml -- run "your task"
```

The full ProtoLink A2A mesh factories are in `protoagent_core/agents/`.
The embedded CLI runtime uses ProtoLink's Registry and `AgentClient`, so
Architect discovers Explorer and Coder through the registry and delegates with
ProtoLink `agent_call` semantics. Architect persists durable conversation
memory; Explorer and Coder are stateless workers for the current run.

Coder tools declare `workspace.write` and build `RunAction` objects with
`Artifact(kind="preview", media_type="text/x-diff")`. Protolink policy pauses
those actions and calls the Rust-owned approval handler before execution. The
same control bridge forwards TUI cancellation through `AgentClient.cancel_task()`.
The embedded in-process fast path uses the same typed `TaskCancellationRequest`
and falls back to the transport control plane when needed.
Agent policies are deny-by-default: Architect explicitly allows delegation and
state operations, Explorer allows only read-only workspace capabilities, Coder
requires approval for workspace writes, and state describe/reset/compact
remains an application control-plane path through ProtoLink rather than a
model-visible tool.

Interactive help is handled by the isolated Guide agent. `/help` remains a
static command panel, while `/help <question>` asks Guide using the active
model. If no model is selected, the CLI shows static help and points the user
to `/model` before offering interactive help.
