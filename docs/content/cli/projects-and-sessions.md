---
title: Projects And Sessions
description: Active project state, file tagging, conversation memory mode, and Rust UI session summaries.
---

ProtoAgent separates the active workspace, UI session summaries, and model-facing
conversation memory. This distinction matters for debugging.

## Active Project

The active project is the root of the workspace boundary. It controls:

1. Which files `@` can tag.
2. Which files Context Loom indexes.
3. Which paths Explorer and Coder tools may read or write.
4. Which project session id is used for ProtoLink memory.

Commands:

```bash
proto-cli project
proto-cli project set ~/projects/my-app
proto-cli project clear
```

TUI:

```text
/project
/project ~/projects/my-app
/project clear
```

The project config is stored at:

```text
~/.protoagent/project.json
```

The config contains:

| Key | Meaning |
| --- | --- |
| `active_project` | Current project path. |
| `recent_projects` | Up to 12 recently opened project paths. |
| `context_memory_enabled` | Optional boolean controlling persistent ProtoLink session use. Defaults to on. |

## Path Resolution

Relative project paths are resolved from the launch workspace. If the CLI is
started from `cli/`, `default_launch_workspace()` recognizes the repo root when
`whitepaper.md` is present.

The core tools use `safe_path()` to reject access outside the selected
workspace.

## File Tags

Tagged context lets the user explicitly include a file or directory in a task:

```text
explain @src/auth.rs
summarize @"docs/space path.md"
```

The Python core loads at most six tags, with a total tagged-context budget. File
tags are highest-priority context and are passed before the automatically chosen
Context Loom evidence.

## Session Ids

The Rust CLI derives a stable project session id from the workspace path:

```text
protoagent-project-<hash>
```

When persistent memory is on, that id is passed to ProtoLink. When memory is
off, the core uses task-local state and does not persist project conversation
continuity.

Commands:

```text
/context on
/context off
/context memory
```

## Rust UI Session Ledger

The Rust UI stores a product-facing session ledger at:

```text
~/.protoagent/sessions.json
```

This is not the model's authoritative conversation memory. It is a UI/history
ledger used for:

1. Recent project session display.
2. Last prompts and answer previews.
3. Provider/model metadata.
4. Event counts and timeline summaries.
5. Reopening a workspace from a saved session.

Commands:

```text
/sessions
/sessions choose
/session rename NAME
/last
```

Shell:

```bash
proto-cli sessions
```

## Model-Facing Memory

Model-facing conversation state lives in ProtoLink storage:

```text
~/.protoagent/conversations.sqlite
```

The Python core controls this state through ProtoLink APIs:

| Command | Core function |
| --- | --- |
| `/context history` | `describe_saved_histories()` |
| `/context compact` | `compact_saved_histories()` |
| `/context reset` | `reset_saved_histories()` |
| automatic run compaction | `compact_agent_histories_for_run()` |

`/context reset` clears ProtoLink conversation histories and trims the Rust UI
session history for the active session to zero stored turns.

## What To Update

If project selection changes, update:

1. `cli/src/main.rs`
2. `cli/src/terminal_ui/project.rs`
3. this page

If session persistence changes, update:

1. `cli/src/sessions.rs`
2. `core/protoagent_core/history.py`
3. this page
4. `Core / State And Memory`
