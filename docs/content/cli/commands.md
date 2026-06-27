---
title: Command Reference
description: Shell commands and TUI slash commands for ProtoAgent.
---

This page documents the current command surface in `cli/src/main.rs` and
`cli/src/terminal_ui.rs`.

## Shell Commands

Run through Cargo while developing:

```bash
cargo run --manifest-path cli/Cargo.toml -- <command>
```

Installed examples use `proto-cli <command>`.

| Command | Meaning |
| --- | --- |
| `start` | Start the fullscreen terminal UI. This is also the default when no command is passed. |
| `tui` | Alias for fullscreen terminal UI. |
| `cli` | Alias for fullscreen terminal UI. |
| `terminal` | Alias for fullscreen terminal UI. |
| `ui` | Alias for fullscreen terminal UI. |
| `run "task"` | Run one task outside fullscreen mode. Requires an active project. |
| `dashboard`, `dash`, `status` | Show cockpit status, provider strip, agent graph, and hotkeys. |
| `models` | Show detected local/API models and validate API keys. |
| `model` | Pick the active provider/model interactively. |
| `key [provider]` | Store an API key for a supported API provider, then optionally choose a model. |
| `config` | Show redacted provider config. |
| `project` | Show active project and recent projects. |
| `project set PATH` | Set the active project folder. |
| `project clear` | Clear the active project folder. |
| `project choose` | Prompt for a project folder. |
| `check` | Show Python, platform, ProtoLink readiness, active provider status, and agent manifest. |
| `agents` | Show Architect/Explorer/Coder topology and tool isolation. |
| `context` | Show Context Loom status for the active project. |
| `context QUERY` | Build a source-cited Context Pack without running a model. |
| `context window [16k|auto]` | Show, set, or clear the Ollama context window override. |
| `context history` | Inspect saved ProtoLink per-agent conversation state. |
| `context compact [recent|tokens|summary] [limit]` | Compact saved ProtoLink histories and trim the Rust session index. |
| `context reset` | Clear ProtoLink histories for the project session and trim Rust UI turns to zero. |
| `context on` | Enable persistent project conversation memory. |
| `context off` | Use task-local ProtoLink sessions until memory is turned on again. |
| `index refresh` | Refresh or rebuild the Context Loom SQLite index. |
| `sessions` | Show saved project sessions and recent turns. |
| `help` | Show static CLI help. |
| `help "question"` | Ask the isolated Guide agent about ProtoAgent usage. |

## Fullscreen Slash Commands

Slash commands are handled inside `cli/src/terminal_ui.rs`.

| Command | Meaning |
| --- | --- |
| `/quit`, `/exit` | Leave the TUI. |
| `/clear` | Clear the visible transcript. |
| `/dashboard`, `/dash`, `/status` | Pin the dashboard panel. |
| `/models` | Pin the model inventory panel and refresh model status. |
| `/models choose`, `/models set`, `/models select` | Open model selection. |
| `/model`, `/provider` | Open provider/model selection. |
| `/key [provider]` | Store an API key through a masked modal. |
| `/agents` | Pin the agent topology panel. |
| `/context`, `/loom` | Show Context Loom status. |
| `/context QUERY` | Preview a Context Pack. |
| `/context window [16k|auto]` | Show or change context window settings. |
| `/context history` | Inspect saved ProtoLink histories. |
| `/context compact [recent|tokens|summary] [limit]` | Compact saved model-facing histories. |
| `/context reset` | Reset saved ProtoLink histories for the active project session. |
| `/context on` | Enable persistent conversation memory. |
| `/context off` | Use task-local memory. |
| `/context memory` | Show current memory mode. |
| `/index refresh` | Refresh Context Loom index. |
| `/sessions`, `/session` | Show saved sessions. |
| `/sessions choose`, `/session resume` | Reopen a saved session's workspace. |
| `/session rename NAME` | Rename the current saved UI session. |
| `/timeline`, `/flow` | Show the structured path of the latest agent run. |
| `/trace` | Show the latest normalized ProtoLink trace. |
| `/diff` | Open the last proposed diff in the review modal. |
| `/diff raw` | Print a truncated raw diff into the transcript. |
| `/config` | Pin provider config panel. |
| `/project`, `/open` | Choose the active project. |
| `/project PATH` | Open a project directly. |
| `/project clear` | Clear the active project. |
| `/help`, `/menu` | Show help panel and Guide availability. |
| `/help QUESTION` | Ask Guide using the active model. |
| `/check` | Refresh runtime diagnostics. |
| `/last` | Replay the last response in the current TUI process. |
| `/run TASK` | Run a task from a slash command. |

## Non-Slash Input

Any non-empty input that does not start with `/` runs as a task. The TUI
requires an active project before dispatching the request.

```text
explain the runtime cancellation path
```

Use `@` to insert tagged file context:

```text
explain @core/protoagent_core/runtime.py and @cli/src/progress.rs
```

Tagged context is loaded before Context Loom and is treated as bounded
read-only evidence unless the user explicitly asks for modifications.

## Compaction Syntax

`/context compact` defaults to token compaction. Supported forms:

| Form | Effect |
| --- | --- |
| `/context compact` | Compact each agent history to the default token budget. |
| `/context compact tokens 4000` | Keep each agent history under roughly 4000 estimated tokens. |
| `/context compact recent 8` | Keep a recent-message boundary. |
| `/context compact summary 6` | Use ProtoLink summary strategy preserving recent messages. |
| `/context compact 3` | Legacy shorthand: keep about three recent UI turns in the Rust ledger and use recent-history compaction. |

## Exit And Cancellation

| Key | Context | Effect |
| --- | --- | --- |
| Esc | TUI idle | Opens the exit confirmation modal. |
| Ctrl-C | TUI idle | Exits the TUI. |
| Esc | Task running | Writes a cancellation request for the Python bridge. |
| Ctrl-C | Task running | Same as Esc: requests cancellation. |
| Ctrl-D | Input empty | Exits the TUI. |

Task cancellation is best-effort and uses ProtoLink's task control path. If a
task is already terminal, the runtime reports that cancellation arrived too late.
