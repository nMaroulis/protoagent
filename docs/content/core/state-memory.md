---
title: State And Memory
description: ProtoLink conversation state, run-boundary compaction, explicit memory commands, and Rust session separation.
---

ProtoAgent has two separate memory layers:

1. Rust UI session summaries in `sessions.json`.
2. ProtoLink model-facing conversation state in `conversations.sqlite`.

The second one is the real agent memory.

## Storage

Conversation state uses ProtoLink `SQLiteStorage`:

```text
~/.protoagent/conversations.sqlite
```

Only the stateful Architect uses durable model-facing conversation storage:

| Role | State model | Namespace |
| --- | --- | --- |
| Architect | durable conversation memory | `protoagent-architect` |
| Explorer | task-local stateless worker | none |
| Coder | task-local stateless worker | none |
| Scout | optional tool-only worker; no LLM state | none |

ProtoLink may allocate in-memory state for Explorer/Coder execution, but that
state is discarded with the task and is not part of `conversations.sqlite`.
Scout is created with no LLM, no storage, and empty state.

The session id is usually derived from the active project path:

```text
protoagent-project-<hash>
```

If `/context off` is active, the Rust CLI passes no stable session id and runs
use task-local state.

## Run-Boundary Compaction

Before a session resumes, `compact_agent_histories_for_run()` checks durable
agent storage and the active `LLMModelProfile.context_window`. In the current
architecture this applies to Architect memory only; stateless workers are
skipped.

Default ratio:

```bash
PROTOAGENT_HISTORY_BUDGET_RATIO=0.7
```

The code clamps the ratio between 0.2 and 0.9.

## Explicit Commands

| CLI command | Python function | ProtoLink operation |
| --- | --- | --- |
| `/context history` | `describe_saved_histories()` | `describe_state()` for Architect memory |
| `/context compact` | `compact_saved_histories()` | `compact_state()` for Architect memory |
| `/context reset` | `reset_saved_histories()` | `reset_state()` for Architect memory |

State control facades are regular ProtoLink `Agent` instances with no LLM. Their
policy allows history/state operations and denies everything else.

## Compaction Strategies

| Strategy | Options |
| --- | --- |
| `recent` | `max_messages` |
| `tokens` | `max_tokens`, `preserve_recent=6` |
| `summary` | `preserve_recent` |

When no token limit is passed, `tokens` uses the Architect model profile and the
history budget ratio. If no context window exists, it falls back to 4000 tokens.

## Top-Level Turn Persistence

`persist_architect_turn()` makes sure the top-level Architect user/assistant
turn exists in ProtoLink conversation state. This guards against gaps when the
streaming runtime does not persist the final current turn.

It avoids duplicates by checking whether the latest saved messages already
match either:

1. The raw user prompt.
2. A runtime prompt that ends with `Current user request: <prompt>`.

## Rust Session Ledger Is Different

`cli/src/sessions.rs` records UI summaries:

| Field | Purpose |
| --- | --- |
| `id` | Stable project-derived id. |
| `name` | Display name. |
| `workspace` | Project path. |
| `turns` | UI turn count. |
| `history` | Recent UI turn previews. |
| `timeline` | Structured timeline derived from run events. |

Limits:

| Limit | Value |
| --- | --- |
| Max saved sessions | 40 |
| Max turns per session | 60 |
| Answer preview chars | 420 |

This ledger is useful for the UI but should not be described as the model's
conversation memory.
