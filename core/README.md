# ProtoAgent Core

Python brain for the ProtoAgent frontends. The Rust CLI imports this package
through PyO3 and expects JSON strings from `protoagent_core.agent_engine`.

Current package version: `0.2.3`. The source of truth is
`core/pyproject.toml`, mirrored by `protoagent_core.__version__`.

Install ProtoLink 0.7.1 or newer with the HTTP and LLM extras:

```bash
pip install "protolink[http,llms]>=0.7.1"
```

Recoverable Coder writes require POSIX. The runtime uses the native execution,
approval, lifecycle, recovery and completion APIs; ProtoAgent owns coding roles,
context, configuration, acceptance criteria and terminal presentation.

## Layout

- `protoagent_core/agent_engine.py` - PyO3-facing functions for prompts, model discovery, config, and doctor checks.
- `protoagent_core/_version.py` - Runtime version metadata and component version inventory for the CLI.
- `protoagent_core/runtime.py` - Embedded ProtoLink mesh runner. It configures `AgentGroup`, `RunHandle`, native storage, scoped approvals and reports.
- `protoagent_core/history.py` - ProtoLink state-operation facade for automatic Architect token-budget compaction plus explicit history/compact/reset commands.
- `protoagent_core/runtime_bridge.py` - Application approval and cancellation bridge for the Rust CLI.
- `protoagent_core/help_agent.py` - Isolated Guide agent for `/help <question>` usage help; it is not registered with the coding mesh and has no tools, delegation, storage, or project session.
- `protoagent_core/command_reference.json` - Packaged TUI/shell reference shared by Guide and Rust command completion. Guide streams via native `AgentGroup`/`RunHandle`, with cancellation and redacted settings on every call.
- `protoagent_core/models.py` - Ollama, LM Studio, OpenAI-compatible, llama.cpp, and API model inventory.
- `protoagent_core/config.py` - Provider, prompt-profile, optional-agent, and API-key config at `~/.protoagent/config.json`.
- `protoagent_core/prompt_profiles.py` - Small/medium/large/API prompt profiles for the agent deck.
- `protoagent_core/quality_eval.py` - Fixed prompt-profile benchmark tasks and scoring helpers.
- `protoagent_core/context/` - Context Loom indexer, SQLite store, and source-cited Context Pack builder.
- `protoagent_core/agents/` - ProtoLink Architect, Explorer, Coder, Verifier, and optional Scout factories. Architect is the stateful controller; all workers are task-local and stateless.
- `protoagent_core/run_contracts.py` - Application intent classification; native completion checks require execution receipts.
- `protoagent_core/tools.py` - Application-specific workspace exploration helpers.

## Verification And File Recovery

Verifier registers ProtoLink `process_tool()` as `execute_command`. Coder
registers `filesystem_tools()` as `create_file`, `replace_file`, `preview_change`
and `restore_change`. Both mutations and restoration require broker approval;
commands require `process.execute` approval of the frozen specification.

`workflow.py` uses Graph for an initial Architect attempt and at most two
repairs. `verification.py` supplies native completion predicates over executed
outcomes and current resource revisions. Approval and diff previews cannot
satisfy completion. All edits precede checks within an attempt.

`checkpoints.py` configures a private native `StorageCheckpointStore` and a
single-writer lease. It contains no file mutation implementation. `/undo` needs
no model. Native recovery requires POSIX and existing parent directories, rejects
symlinks and changed revisions, and does not roll back command effects. Legacy
v0.2.1 snapshots remain inspection-only in the original database.

`SQLiteRunStore(..., redaction_policy=...)` handles persistence redaction,
including automatic snapshots and caller metadata. `runtime_storage.py` selects
application credentials for native `sensitive_values` masking and strips terminal
controls. Native parent reports already include delegated worker receipts;
completion no longer scans stored worker tasks. Checkpoint inventory uses native
`list_changes()` filters and pagination. RunReplay is read-only inspection.
See the [runtime guide](../docs/content/core/runtime.md) and
[verification/recovery manual](../docs/content/cli/verification-and-recovery.md).

## Provider Execution

The CLI invokes the selected provider/model through ProtoLink agents by
default. The selected model is used to create fresh LLM instances for
Architect, Explorer, and Coder on each run. Verifier is always registered with
no LLM and exposes approved command execution. Scout is a tool-only agent with no
LLM and is not constructed or registered when it is disabled. Agents use ProtoLink's SSE
JSON-RPC lifecycle-aware task stream by default, while the Registry remains on
plain HTTP.

Each LLM is configured through `LLM.configure_metrics(LLMModelProfile(...))`.
For Ollama, the same selected window is sent as `num_ctx` and recorded in the
profile, so ProtoLink's `context.prepared`, `llm_context`, and
`llm_call_metrics` events drive the terminal context meter. Conversation
continuity lives in ProtoLink's Architect SQLite state. Explorer and Coder use
task-local in-memory state; Scout has no model state. Worker calls therefore do
not accumulate durable conversation history. Before a session resumes, the core uses
`Agent.compact_state(strategy="tokens")` when the Architect history budget is
exceeded; `/context history`, `/context compact`, and `/context reset` use
ProtoLink state operation reports. `/context on` and `/context off` control
whether Rust passes the stable project session ID or a task-local session to
ProtoLink. The Rust trace and timeline views consume normalized `RunEvent`s
first, including causal IDs for nested routes, and each run returns a redacted
ProtoLink `RunReport` for diagnostics and replay.

Before each model run, Context Loom incrementally refreshes its deterministic
local index and injects a bounded Context Pack into the Architect prompt.
Unchanged files are identified from stored size and modification-time metadata,
so they are not reread, reparsed, or upserted; new/changed files are processed
and stale entries are removed. Explorer also exposes `build_context_pack` as a
ProtoLink tool so the agent mesh can ask for focused evidence during a run.

Agent prompts are tuned through a configurable prompt profile:
`auto`, `small`, `medium`, `large`, or `api`. `auto` resolves from the active
provider/model. The profile changes only the role instructions for the enabled
LLM agents; ProtoLink still owns delegation, tools, memory, policies, runtime
events, and reports. A ProtoAgent `RunContract` is inferred before the model
runs, attached to `RunContext.metadata`, and checked with native
`CompletionValidator`. Write tasks need an executed native change at its current
revision. Missing or stale evidence remains incomplete; denials and uncertain
effects stop repair routing.

Prompt profile quality can be checked with the built-in eval harness:
`proto-cli eval profiles` runs a scaffold smoke without contacting a model,
while `proto-cli eval profiles --live` runs the selected model with write
approvals auto-denied.

Useful runtime switches:

- `PROTOAGENT_STREAM=0` suppresses live text and incremental UI summaries; native handles still consume execution once.
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
Architect discovers enabled workers through the registry and delegates with
ProtoLink `agent_call` semantics. Architect persists durable conversation
memory; Explorer, Coder, and optional Scout are stateless workers for the
current run.

Scout is disabled by default through `optional_agents.scout.enabled`. It can be
toggled with `proto-cli agents scout on|off` or `/agents scout on|off`; changes
apply to the next run. When enabled, Scout receives ProtoLink 0.7.1's
`web_search` and `fetch_url` tools with the `network.read` capability. It has no
workspace tools. Brave search reads `BRAVE_SEARCH_API_KEY` only when invoked;
DuckDuckGo is keyless best-effort search, and English Wikipedia is keyless
factual search. Registration itself performs no network request, and returned
content is bounded and marked untrusted.

Coder tools declare `filesystem.write` and `filesystem.restore`; both require
approval. The actual handler is `ApprovalBroker`. Rust presents the native
preview and echoes the exact request ID/fingerprint. Application authentication
constructs `ApprovalScope`; cancellation goes through `RunHandle.cancel()`.
Agent policies are deny-by-default: Architect explicitly allows delegation and
state operations, Explorer allows only read-only workspace capabilities, Coder
requires approval for workspace writes, and Scout allows only `network.read`.
State describe/reset/compact remains an application control-plane path through
ProtoLink rather than a model-visible tool.

Interactive help is handled by the isolated Guide agent. `/help` remains a
static command panel, while `/help <question>` asks Guide using the active
model. If no model is selected, the CLI shows static help and points the user
to `/model` before offering interactive help.
