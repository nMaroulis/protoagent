---
title: ProtoLink Engine Integration
description: How ProtoAgent uses native execution, action correction, capability selection, local supervision and evaluation.
---

ProtoLink is ProtoAgent's execution engine. The core requires
`protolink[http,llms,mcp]>=0.8.2`; CLI, core and documentation remain at **0.3.1**.
The coding harness defines task state, evidence, editing scope and acceptance.
The engine owns action dispatch, authorization, child execution, budgets,
cancellation, observations and execution receipts.

## Integrated capabilities

| ProtoLink API | ProtoAgent use |
| --- | --- |
| `Agent(subagents=..., subagent_limits=...)` | Default local Architect roster; ordinary `agent_call` actions launch owned children without a Registry or server. |
| `SubagentLimits` | One active child, depth one, 32 children per attempt by default; no background supervision tools in the model prompt. |
| Shared root budgets and inherited policies | Children consume the Graph's shared budget and must satisfy both the Architect's capability ceiling and their own role policy. |
| `ContextPolicy` | Reserve output space, prune complete old turns, clear older observations and offload large tool results to scoped artifacts. |
| `AgentHooks.before_model` | Refresh the application TaskRecord, compact small-profile declarations, filter redundant worker tools, prepare delegated observations and account for native schemas. Provider acquisition methods are not wrapped. |
| `Agent.add_worker_tool` | Narrow typed worker declarations for small local runs. ProtoLink resolves each alias to its original owned child with inherited policy, budgets and receipts; no application dispatcher or preflight wrapper. |
| `yield_after_steps`, `RunHandoff`, `resume_task` | Local Coder work slices use temporary native execution checkpoints. Application policy grants progressing slices; the SDK preserves history, committed actions, correction allowances and cumulative budgets. |
| Shared action parser and native normalization | Correct recognizable malformed requests inside the current run in JSON/native modes; preserve completed observations and legitimate explicit final data. |
| `tool_calling`, `resolve_tool_calling()`, `refresh_capabilities()` | Ollama defaults to engine-owned automatic discovery; authenticated exact-tag metadata, five-minute cache, JSON for unknown support and explicit mode overrides. |
| `ToolRejectedError`, `max_tool_corrections` | Invalid plans, exact-edit preconditions and check selection return explicit no-change feedback. Native correction is bounded, retains receipts and emits distinct rejection events/artifacts. |
| `AgentCapabilities.supports_inference` | Native cards distinguish reasoning workers from tool-only Verifier/Scout/MCP. The prepared local facade and generic delegation validate targets, tools and schemas against these original cards before dispatch. |
| `read_context_artifact` | Progressive retrieval of offloaded results. Offloading changes model observations; existing execution receipts retain their authority. |
| `RoutedLLM` | Optional, explicit same-provider `fallback_models`; no extra model orchestrates selection. Each candidate request consumes the shared budget. |
| `ask_user_tool`, `UserInputRequest` | Architect-only live clarification; the CLI/TUI returns an answer through a correlated callback and the native loop continues. |
| `evaluate()` | `proto-cli eval harness` runs repeatable offline source-read/task-state cases with fresh factories and linked child receipt checks. |
| `AgentGroup`, `RunHandle`, `Graph` | Owned lifecycle, normalized results/events, cancellation and bounded editing followed by one software-scheduled check pass. |
| `ApprovalBroker`, `filesystem_tools`, `process_tool` | Exact native approvals, prepared file changes/recovery and bounded approved host commands. |
| Native MCP adapter | Optional lazy broker discovery and allowlisted calls, with managed sessions and approvals. |

Local delegation retains the same action protocol and agent names. Optional
Tester/Verifier/Scout/MCP controls still remove disabled workers from construction and the
roster. Verifier, Scout and MCP remain model-free. Serial delegation avoids
concurrent model requests competing for one local runner and gives the edit/check
phase one clear owner. The numerical child limit is configurable through
`PROTOAGENT_MAX_CHILDREN`; it is separate from the shared step/tool/token budgets.

Set `PROTOAGENT_AGENT_TRANSPORT=sse`, `http`, `websocket`, `grpc` or `runtime` to
use the explicit Registry/transport mesh. JSON-RPC aliases still map to SSE.
These paths retain native transport authentication and diagnostics; they do not
have the local supervisor's aggregate child accounting contract.

## Correction and engine ownership

The application no longer probes or caches Ollama tool capability independently,
and no longer aborts malformed proposals through a final-answer hook. ProtoLink
owns normalization, parse correction, protocol selection and request correction.
The before-model hook still prepares coding context and neutralizes malformed
legacy assistant messages using the native parser; explicit final data is retained.

`plan_task`, invalid worker packets/reports, `edit_file` source checks and
`run_check` selection use `ToolRejectedError` only before any mutation or
external effect. The application supplies its error code, valid IDs and concise
guidance; the engine supplies the observation, counter, event and receipt.
Ordinary exceptions, denied approvals, interrupted writes and failed MCP calls
retain their failure behavior. A rejected request never establishes completion.

The correction allowance defaults to two per inference and is shared across
local and delegated request errors. Configure `providers.PROVIDER.max_tool_corrections`
to change it. Successful actions do not reset it, and correction consumes the
existing model-call/output/runtime budgets. Parse errors have the engine's
separate parse circuit breaker. Neither path restarts the coding Graph.
Preparation-stage `ToolRejectedError` now enters this same native correction
loop before approval or execution and consumes no tool call. Source validation
no longer fabricates an executable read-validation action. Ordinary exceptions
remain failures; only explicit no-change rejections are correctable.

## Live child observations

ProtoLink forwards owned child events immediately through `RunHandle.events()`,
including nested children. The frontend consumes that public stream and deduplicates
presentation by native event ID. It no longer polls Task metadata or maintains a
second child-event reader. Native reports remain authoritative.

## Live checkpointed worker execution

Local coding runs configure `yield_after_steps=9` on Coder and a private temporary
`SQLiteDurableStore` on its Architect owner. At a native yield, the controller
checks fresh applied writes, current revisions, worker reports and stop conditions
before granting `resume_task(root_run_id)`. It admits up to three slices per child
assignment; `PROTOAGENT_CODER_MAX_WINDOWS` can lower that allowance. SDK approval
and question interruptions use the existing scoped broker and question UI before
an exact resume response. No approval or input is inferred from an interruption.
All model/tool work remains charged to the shared hard budget. Checkpointed
approval/input resumes can also start a new bounded slice; the setting is not a
fixed total model-call count. Already committed edits and approvals are not replayed.

The Graph and TaskRecord remain live application state. These execution stores
are private, temporary and removed at workflow exit; a saved conversation or run
report cannot restart the coding workflow. Explicit transport meshes retain live
bounded workers; `PROTOAGENT_CHECKPOINTED_WORKERS=0` selects that fallback locally.

## Deliberate integration boundaries

| Engine capability | Current application boundary |
| --- | --- |
| Cross-process durable execution and `RunManager` | Native execution checkpoints support live local worker slices. Restoring the whole coding Graph after exit remains unsupported because its TaskRecord, frozen check plan and phase state are not persisted for reconstruction. |
| Background children and model supervision tools | Disabled. Small models keep one action at a time; background orchestration adds handles, waits and unresolved jobs to their decisions. |
| Per-step model routing | The application uses an explicit fallback list, with no reasoning-based escalation or cross-provider migration. |
| Docker execution backend | Not wired to the coding checks. Host argv/cwd/env and absolute interpreter paths need an explicit container check contract before they can be mapped safely. |
| Research/Knowledge/Database/Explorer presets | Available upstream. Existing coding roles retain their source revisions, Context Loom, TaskRecord tools and authority boundaries. Optional Scout already uses native web tools without an LLM. |

Saved conversation memory, diagnostic reports and file restoration are distinct
from durable execution continuation. `session resume` reopens a UI session;
`undo` restores a native file checkpoint. Neither resumes an interrupted coding
run. Unknown external outcomes are inspected, never automatically replayed.
Local checkpointed questions continue through the SDK resume API; live fallback
runs use the SDK callback. Both preserve exact question correlation and return
answers without expanding execution authority.

## Provider SDKs and model input

ProtoLink owns Anthropic v1 compatibility, response failure propagation, native
call-count validation and tokenizer control-string handling. The application
adds no provider SDK upper bound. Configured Gemini, OpenAI and Anthropic API
URLs are forwarded to the selected native adapter. Telemetry integrations remain
optional; SDK support for Langfuse 4/5 or LangSmith does not enable them by default.

## Validation

```bash
proto-cli check
proto-cli eval harness --json
PYTHONPATH=core .venv/bin/python -m unittest discover -s core/tests -q
cargo test --locked --manifest-path cli/Cargo.toml
```

Offline checks exercise native approvals, local/transport delegation, independent
child conversations, inherited denial, shared budgets, child limits, cancellation
of an owned process, live output, large-result retrieval, receipt preservation,
request overflow, transient model fallback, single-pass verification, question continuation,
skip, timeout, cancellation, stale-answer rejection and the separation between
feedback and execution approval. Scripted actions
verify integration contracts; they do not measure small-model reasoning quality.
Regression cases also cover malformed-envelope correction and exhaustion,
capability discovery through the engine, tools-only inference rejection,
invented worker tools, preservation/deduplication of completed reads, shared
correction limits, ordinary exception/denial behavior and rejected edit/check
preparation without an approval or effect.
Use [coding evaluations](quality-evals.md) for independently accepted changes.
