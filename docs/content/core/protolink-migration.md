---
title: ProtoLink 0.7 Integrations
description: ProtoAgent 0.2.3 native delegation, redaction, checkpoint inventory and live streaming integrations.
---

## Execution migration in 0.2.2

| ProtoAgent 0.2.1 plumbing | ProtoAgent 0.2.2 integration |
| --- | --- |
| `run_command`, custom subprocess drain/kill/timeout code | Native `process_tool()` / `execute_command`, process events and structured results |
| Manual start/readiness/stop lists | Native `AgentGroup`, explicit owned/external resources |
| Transport event-shape and final-answer reconstruction | Native `RunHandle`, `RunResult`, `RunReport`, cancellation |
| Application approval callback waits | Actual `ApprovalBroker`; Rust adapter resolves exact ID/fingerprint in trusted scope |
| Diff builders, hash checks, custom SQLite checkpoints and file replacement | Native `filesystem_tools()` plus `StorageCheckpointStore` over dedicated `SQLiteStorage` |
| Coder revision counter and preview-based completion guard | Native `ResourceRevision`, `CompletionCheck`, `CompletionValidator` |
| Prompt-only two-repair instruction | Native Graph total/per-node limits and shared workflow budgets |

## Application responsibilities

ProtoAgent retains coding roles, prompts, Context Loom, repository conventions,
configuration, credentials, per-agent LLM construction, command selection,
acceptance predicates, edit/check phases and repair routing. Rust owns the
terminal interface. The application configures roots, storage ownership,
redaction and authentication; it does not provide a second execution engine.

Native capabilities are explicitly gated: `process.execute`, `filesystem.write`
and `filesystem.restore` require approval. Commands have explicit environment,
timeout and output limits. Recovery storage uses private permissions and a
single-writer project lease. Existing conversation/state APIs, native transport
configuration, budgets, RunStore and read-only replay remain in use.

## Native integrations in 0.2.3

ProtoLink 0.7.1 closes the three integration gaps identified in 0.2.2:

| Removed application adapter | Native API now used |
| --- | --- |
| `ApplicationRunStore.trace_report()` / stored-worker scans | Delegated events in parent tasks and `RunReport.from_task()` |
| `ApplicationRunStore.save_task()` / `save_report()` overrides | `SQLiteRunStore(..., redaction_policy=...)` |
| Custom literal credential masking | `RedactionPolicy.sensitive_values` |
| `changes()` / direct `Storage.load()` inventory | `StorageCheckpointStore.list_changes()` with filters and pagination |

Native delegation preserves worker event/run/task/action identities and links to
calling actions. Parent receipts are deduplicated by ProtoLink, and child final
markers do not terminate the parent. The native Graph retains evidence across
bounded repair attempts without querying worker databases.

Native persistence masks task/report/caller metadata copies, including automatic
intermediate snapshots. ProtoAgent selects configured secret values and strips
terminal controls for its UI. Approval and recovery records retain the protected
original data needed for authorization and restoration. Inventory returns those
protected records; the CLI projects metadata only, never original bytes.

## Streaming in 0.2.3

All coding deck agents advertise streaming. ProtoLink 0.7.1 reads HTTP and SDK
streams without blocking the application's event loop. ProtoAgent consumes
`RunHandle.events()` and forwards `llm_chunk`, `llm_final` and `process.output`
to the Rust live preview independently of trace-summary limits.

Shell output flushes as text arrives. The TUI keeps bounded previews by native
run/task/agent/step/channel identity and replaces generation text on `llm_final`.
Only the terminal task result determines success, failure, cancellation or
uncertainty. JSON-action fragments are provisional generation output, not
executable actions. `PROTOAGENT_STREAM=0` disables live presentation without
resubmitting or changing execution. Native peer capabilities determine delegated
streaming versus request/response delivery.

## Remaining boundaries

- **Chained restoration:** restoring a later edit changes native file identity.
  An older checkpoint for the same file can conflict even when its content is
  restored. The application preserves this conservative behavior.
- **Platform and parents:** recoverable file tools require POSIX, existing
  parents and paths without symlinks. They do not create directory trees.
  These are documented boundaries, not app-side mutation fallbacks.

The local backend is host execution without isolation. A lost response or
interrupted checkpoint can mean the effect occurred. Neither transport recovery
nor a repair loop replays that work. Old v0.2.1 checkpoint databases remain
inspection-only because they lack native revision identities.

## Provider-free validation

Integration tests exercise native command execution, denial before effects,
exact approval correlation, simultaneous pending requests, cancellation, output
caps, budgets, stale preimages, restoration conflicts, uncertainty after effects,
private persistence, read-only legacy inventory, normalized final results,
managed cleanup and a maximum of two repairs. The embedded mesh test uses
scripted ProtoLink MockLLM responses and real native tools; no paid models are
needed. Gated HTTP provider tests prove early delivery in JSON-action and native-tool
modes, cancellation while generation waits, resource cleanup and disabled previews.
Runtime and real SSE meshes verify delegated stdout before process completion,
receipt deduplication and bounded repair without stored-task scans. Inventory tests
cover pagination beyond 100 records and uncertainty outside the first page. Rust
tests cover incremental previews, final replacement, partial UTF-8/JSONL reads,
output bounds, native approval previews and fingerprint echoes.

```bash
PYTHONPATH=core .venv/bin/python -m unittest discover -s core/tests -q
cargo test --locked --manifest-path cli/Cargo.toml
```
