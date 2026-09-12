---
title: ProtoLink 0.7 Migration
description: ProtoAgent 0.2.2 migration mapping, removed plumbing and remaining integration boundaries.
---

## Old → new

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

## Concrete integration gaps and limits

- **Delegated receipts:** ProtoLink 0.7.0 model delegation returns worker output
  without merging native child execution events into the parent report.
  ProtoAgent joins same-trace native task snapshots from its RunStore by event
  ID. Delegated process output may therefore arrive after worker completion.
  Parent report/event propagation would remove this adapter.
- **Persistence redaction:** automatic `Agent.run_store` snapshots call
  `save_task` without a configurable redaction policy, and SQLiteRunStore's
  save methods accept no redaction argument. A small application subclass
  persists redacted copies through the native store. A native default redactor
  for persistence would remove that wrapper.
- **Checkpoint inventory:** `StorageCheckpointStore` exposes `get`/`save`, with
  no list API. The CLI reads records through the backing Storage's public
  `load()` API. A native metadata inventory would remove this coupling.
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
needed. Rust tests cover native JSON and diff previews and fingerprint echoes.

```bash
PYTHONPATH=core .venv/bin/python -m unittest discover -s core/tests -q
cargo test --locked --manifest-path cli/Cargo.toml
```
