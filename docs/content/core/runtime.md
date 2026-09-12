---
title: Runtime
description: AgentGroup lifecycle, scoped approvals, native RunHandle results, bounded coding workflows and reports.
---

ProtoAgent 0.2.2 requires **ProtoLink 0.7.0**. `runtime.py` configures an embedded
mesh; `workflow.py` defines coding acceptance and repair routing. Native agents,
tools, policies, budgets, storage and cancellation execute the work.

## Entry and ownership

```python
run_selected_model(prompt, workspace=None, session_id=None, progress_path=None, user_prompt=None)
```

The application loads model configuration, builds a trusted `RunContext`, leases
the project's checkpoint namespace, and constructs its agents. Each LLM-capable
role gets its own configured LLM instance. Verifier and optional Scout have no
LLM. Architect retains its conversation state; workers remain task-local.

| Native object | Application configuration |
| --- | --- |
| `AgentGroup` | Owns the Registry and enabled mesh agents; readiness, partial-start rollback and shutdown are native. |
| Second `AgentGroup` | Owns a local workflow controller, declaring the running mesh as external resources. |
| `RunHandle` | Executes the workflow and Architect attempts; supplies typed events, cancellation, normalized `RunResult` and `RunReport`. |
| `ApprovalBroker` | Actual approval handler on Coder/Verifier, with per-run durable records and expiration. |
| `StorageCheckpointStore` | Dedicated per-project `SQLiteStorage` namespace under `recovery/`. |
| `SQLiteRunStore` | Native task/report persistence in a private per-run database under `runs/`. |
| `Graph` | One initial attempt, two repair visits, three acceptance visits, seven total visits maximum. |
| `CompletionValidator` | Application predicates over native execution receipts and resource revisions. |

The tool-only workflow controller exposes an application `run_workflow` tool to
its local handle. It has no model and is not advertised in worker discovery.
There is no application subprocess launcher or transport-final-event parser.

## Bounded application workflow

```mermaid
flowchart LR
  I[Initial Architect attempt] --> A[Native completion validation]
  A -->|Satisfied or requires inspection| D[Return result]
  A -->|Completed nonzero check| R[Repair: at most 2 visits]
  R --> A
```

Architect receives repository context and delegates through normal ProtoLink
`agent_call` execution. All edits precede command checks within one attempt.
The application policy prevents further file changes after checking starts.
Only Graph dispatch opens a new repair attempt. Graph limits and shared native
budgets bound repair work independently of `RetryPolicy`.

`run_contracts.py` classifies the original prompt. Write contracts require a
native applied-file receipt at its current revision; verification requests
require executed commands. Approval, previews, delegation and blocker prose
cannot satisfy execution checks. Structured denials and blockers are preserved
as unsuccessful outcomes. A write without any command is explicitly unverified.

Command acceptance records the revisions of this run's changed files at
preparation and rechecks them through `CompletionCheck.read_revision`. It detects
later changes to those resources, including external edits. It does not capture
all repository inputs. See [Verify & Recover](../cli/verification-and-recovery.md)
for the exact acceptance and restoration boundaries.

## Authorization and controls

The top-level context grants `agent.delegate`, `workspace.read`,
`filesystem.read`, `filesystem.write`, `filesystem.restore`, `process.execute`
and `network.read`. Each agent still applies a restrictive native capability
policy. Coder requires approval for writes and restoration; Verifier requires
approval for command execution. Native policies otherwise allow actions by
default, so these requirements are explicitly configured.

`RunAuthorization` admits descendant run IDs from this owned, API-key-authenticated
mesh's trace and workspace. It constructs `ApprovalScope` server-side.
`RuntimeBridge` presents one pending broker request at a time and resolves the
exact request ID and prepared-action fingerprint. UI JSON cannot enlarge the
scope. Multiple pending requests remain in the broker. Malformed or stale
decisions do not release execution. Cancellation is sent once to `RunHandle`;
the application does not retry task submission to recover an uncertain effect.

Controls use short-lived private files shared with Rust. Without an interactive
bridge, pending approvals are denied. The per-run broker namespace has one live
writer; reopening stored pending records is inspection-only uncertainty, never
execution resumption.

## Transports and events

The mesh defaults to loopback SSE with an HTTP Registry. Set
`PROTOAGENT_AGENT_TRANSPORT=http`, `websocket`, `grpc` or `runtime` as needed.
The `grpc` transport requires ProtoLink's optional extra. `runtime` uses an
in-process Registry and transport identities. JSON-RPC aliases map to SSE.

`PROTOAGENT_RUNTIME_HOST` defaults to `127.0.0.1`. Registry and worker URLs can
be overridden with `PROTOAGENT_REGISTRY_URL`, `PROTOAGENT_ARCHITECT_URL`,
`PROTOAGENT_EXPLORER_URL`, `PROTOAGENT_CODER_URL`, `PROTOAGENT_VERIFIER_URL` and
`PROTOAGENT_SCOUT_URL`; the corresponding older `*_AGENT_URL` aliases remain.
The entry handle invokes owned agents locally; there is no separate CLI task
client to configure.

ProtoLink handles task streams and final-result normalization. ProtoAgent only
formats typed events for the UI, suppresses token chunks, and limits visible
summaries with `PROTOAGENT_STREAM_TRACE_LIMIT` (default 120).
`PROTOAGENT_STREAM=0` suppresses incremental UI summaries; native handles still
consume execution to completion. It does not resubmit work on another transport.

**0.7.0 integration gap:** model delegation returns a worker output without
merging that worker's native receipts into the parent report. The application
composes native task snapshots from the same trace in its `SQLiteRunStore` by
event ID. It never turns model tool-result prose into evidence. Delegated process
output may therefore be available only once the worker snapshot is persisted;
direct native process handles expose live `process.output` events.

Responses retain native transport diagnostics for the Registry and each worker.
The final report keeps the handle's normalized terminal task plus application
status, completion validation and verification metadata. Failed and uncertain
results remain failed and uncertain in the CLI. Errors after submission are
reported as potentially effected work, never as an action that was not submitted.

## Budgets and persistence

| Environment variable | Native budget |
| --- | --- |
| `PROTOAGENT_RUN_MAX_STEPS` | `max_steps`; application default 80 |
| `PROTOAGENT_RUN_MAX_LLM_CALLS` | `max_llm_calls` |
| `PROTOAGENT_RUN_MAX_TOOL_CALLS` | `max_tool_calls`; application default 80 |
| `PROTOAGENT_RUN_MAX_SECONDS` | `max_runtime_seconds`; default agent timeout, 600 seconds |
| `PROTOAGENT_RUN_MAX_INPUT_TOKENS` | `max_input_tokens` for non-Ollama providers |
| `PROTOAGENT_RUN_MAX_OUTPUT_TOKENS` | `max_output_tokens` |

Ollama's input budget uses its configured context window. Command execution is
also bounded by its explicit limits and the remaining native runtime budget.
Native nested flows share budgets; remote workers enforce inherited limits.

`ApplicationRunStore` adds mandatory output redaction to native persistence and
composes same-trace receipts. Known credential values and keys, recovery
`data_base64`, and terminal controls are removed from presentation snapshots.
The complete recovery and approval records live only in protected storage.
RunReplay remains read-only inspection, not task resumption.

`PROTOAGENT_TRACE=1` enables native `LocalTraceTelemetry` at
`${PROTOAGENT_CONFIG_DIR:-~/.protoagent}/traces.jsonl`, using the same redactor.
Private directories use 0700 and storage files use 0600. Source diffs and unknown
secrets printed by project code can still be sensitive.
