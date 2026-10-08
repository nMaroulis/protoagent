---
title: ProtoLink Engine Integration
description: How ProtoAgent uses ProtoLink 0.8 execution, local supervision, context policies, routing and evaluation.
---

ProtoLink is ProtoAgent's execution engine. The core requires
`protolink[http,llms,mcp]>=0.8.0`; CLI, core and documentation remain at **0.3.0**.
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
| `AgentHooks.before_model` | Refresh the application TaskRecord, compact small-profile declarations, prepare delegated observations and account for native schemas. Provider acquisition methods are not wrapped. |
| `AgentHooks.before_complete` | Reject an unfinished action returned as answer text before final output and conversation commit; no second action dispatcher or automatic replay. |
| Ollama `supports_tool_calling` | Native tools for models advertising the capability; bounded cached metadata discovery, JSON fallback and explicit mode overrides. |
| `read_context_artifact` | Progressive retrieval of offloaded results. Offloading changes model observations; existing execution receipts retain their authority. |
| `RoutedLLM` | Optional, explicit same-provider `fallback_models`; no extra model orchestrates selection. Each candidate request consumes the shared budget. |
| `ask_user_tool`, `UserInputRequest` | Architect-only live clarification; the CLI/TUI returns an answer through a correlated callback and the native loop continues. |
| `evaluate()` | `proto-cli eval harness` runs repeatable offline source-read/task-state cases with fresh factories and linked child receipt checks. |
| `AgentGroup`, `RunHandle`, `Graph` | Owned lifecycle, normalized results/events, cancellation and bounded edit/check/repair execution. |
| `ApprovalBroker`, `filesystem_tools`, `process_tool` | Exact native approvals, prepared file changes/recovery and bounded approved host commands. |
| Native MCP adapter | Optional lazy broker discovery and allowlisted calls, with managed sessions and approvals. |

Local delegation retains the same action protocol and agent names. Optional
Tester/Scout/MCP controls still remove disabled workers from construction and the
roster. Verifier, Scout and MCP remain model-free. Serial delegation avoids
concurrent model requests competing for one local runner and gives the edit/check
phase one clear owner. The numerical child limit is configurable through
`PROTOAGENT_MAX_CHILDREN`; it is separate from the shared step/tool/token budgets.

Set `PROTOAGENT_AGENT_TRANSPORT=sse`, `http`, `websocket`, `grpc` or `runtime` to
use the explicit Registry/transport mesh. JSON-RPC aliases still map to SSE.
These paths retain native transport authentication and diagnostics; they do not
have the local supervisor's aggregate child accounting contract.

## Live child observations

ProtoLink 0.8.0 records local child output in the parent's native Task before
joining the child. Some events reach the outer stream at join. ProtoAgent's
presentation adapter reads those already-recorded delegated events in bounded
batches, deduplicates event IDs against `RunHandle.events()`, and forwards live
model/process text. This adapter neither dispatches actions nor supplies
completion evidence of its own. Native reports remain authoritative.

## Deliberate integration boundaries

| Engine capability | Current application boundary |
| --- | --- |
| Durable execution, `SQLiteDurableStore`, `RunManager` | Not enabled for the coding workflow. ProtoLink recovery covers its default engine and blocking local children; the application Graph also carries edit/check phase state and custom orchestration. A durable tool around that Graph would not make those effects safely resumable. |
| Background children and model supervision tools | Disabled. Small models keep one action at a time; background orchestration adds handles, waits and unresolved jobs to their decisions. |
| Per-step model routing | The application uses an explicit fallback list, with no reasoning-based escalation or cross-provider migration. |
| Docker execution backend | Not wired to the coding checks. Host argv/cwd/env and absolute interpreter paths need an explicit container check contract before they can be mapped safely. |
| Research/Knowledge/Database/Explorer presets | Available upstream. Existing coding roles retain their source revisions, Context Loom, TaskRecord tools and authority boundaries. Optional Scout already uses native web tools without an LLM. |

Saved conversation memory, diagnostic reports and file restoration are distinct
from durable execution continuation. `session resume` reopens a UI session;
`undo` restores a native file checkpoint. Neither resumes an interrupted coding
run. Unknown external outcomes are inspected, never automatically replayed.
Live questions do not require engine durability: the native tool awaits the UI
callback inside the current task. Its checkpointed restart mode remains separate
from this application's live integration.

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
request overflow, transient model fallback, bounded repairs, question continuation,
skip, timeout, cancellation, stale-answer rejection and the separation between
feedback and execution approval. Scripted actions
verify integration contracts; they do not measure small-model reasoning quality.
Use [coding evaluations](quality-evals.md) for independently accepted changes.
