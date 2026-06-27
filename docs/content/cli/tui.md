---
title: Fullscreen TUI
description: The fullscreen terminal interface, panels, input, scrolling, modals, and rendering architecture.
---

The fullscreen TUI is a terminal takeover UI built with `crossterm`. It uses an
alternate screen, raw mode, mouse capture, and fixed layout regions.

## Layout

| Region | Rows | Responsibility |
| --- | --- | --- |
| Header | 9 rows | Brand bar, active model, pinned status panel, command bar. |
| Transcript | Flexible | User prompts, system progress, trace summaries, assistant responses, metadata. |
| Input area | 4 rows | Prompt line, context meter, project/chat/activity status. |

The shell scrollback is not used during fullscreen mode. The transcript owns
its own scroll offset.

## Panels

The pinned panel is a compact status dashboard at the top of the screen.

| Panel | Shows |
| --- | --- |
| Dashboard | Project, model inventory summary, agent roles, last query, UI mode. |
| Project | Active project, state, `/project` commands, file-tagging hint, project config path. |
| Models | Active provider/model, inventory status, provider chips, setup commands, config path. |
| Agents | Architect, Context Loom, Explorer, Coder, approval boundary. |
| Context | Context Loom, context window, memory commands, pack preview, index refresh. |
| Sessions | Current session, store path, recent sessions. |
| Timeline | Latest structured agent path. |
| Check | Runtime readiness, active provider, workspace, config. |
| Config | Provider/model/config/key hints. |
| Help | Command map, scrolling, cancellation, launch forms. |

The command bar exposes quick panel commands:

```text
/dashboard /project /models /agents /context /sessions /timeline /check /config /help
```

## Input Editor

The input editor is single-line and stable across terminal widths.

| Key | Behavior |
| --- | --- |
| Enter | Submit the line. |
| Left / Right | Move cursor. |
| Home / End | Move to start/end. |
| Backspace / Delete | Edit characters. |
| Up / Down | Navigate input history. |
| Tab | Insert two spaces. |
| `@` | Open the project file picker when a project is active. |

Input history is stored in memory for the current TUI process and capped by
`INPUT_HISTORY_CAPACITY`.

## File Tag Picker

Typing `@` opens a searchable modal backed by
`cli/src/terminal_ui/project.rs`.

The picker:

1. Scans the active project for taggable text files.
2. Skips hidden and ignored directories such as `.git`, `.venv`,
   `node_modules`, `target`, `dist`, and `build`.
3. Skips common binary suffixes.
4. Lets the user type to filter results.
5. Inserts either `@path` or `@"path with spaces"` into the prompt.

The Python core later resolves those tags with `safe_path()` and loads bounded
read-only context.

## Scrolling

| Input | Behavior |
| --- | --- |
| Mouse wheel up/down | Scroll chat by a small fixed amount. |
| PageUp / PageDown | Scroll by visible page size. |
| Ctrl-Home | Jump far up. |
| Ctrl-End | Return to live bottom. |

When scrolled away from the bottom, the TUI renders a `CHAT SCROLLED` marker and
keeps the live task progress from unexpectedly moving the user's viewport.

## Modals

Focused modal modules keep the TUI maintainable:

| File | Modal or flow |
| --- | --- |
| `modal.rs` | Shared modal primitives, text input, searchable picker. |
| `project.rs` | Project folder prompt and file picker. |
| `model_picker.rs` | Provider/model picker and masked API key prompt. |
| `approval.rs` | Runtime approval prompt for ProtoLink actions. |
| `diff_view.rs` | Diff review modal for proposed file changes. |

## Context Meter

The bottom input area contains a context usage meter. It consumes normalized
ProtoLink `RunEvent` payloads, especially model context events, and records:

| Field | Meaning |
| --- | --- |
| `used_tokens` | Estimated or measured tokens used. |
| `window_tokens` | Configured or detected context window when known. |
| `used_percent` | Pressure against the window. |
| `estimated` | Whether token count is estimated. |
| `agent_name` | Agent that emitted the sample. |
| `model` | Model name associated with the sample. |

The meter keeps the latest sample and a high-water peak.

## Task Loop

When a user submits a task:

1. The TUI verifies an active project.
2. A project session id is selected unless `/context off` is active.
3. A temp progress JSONL file is created.
4. Python is called through `call_process_prompt_with_progress`.
5. The TUI tails progress events every 120 ms.
6. Approval requests are displayed as modals.
7. Esc or Ctrl-C writes a cancellation request.
8. The final JSON response is parsed into `CoreResponse`.
9. The response is appended to the transcript and recorded in `sessions.json`.

The one-shot runner in `main.rs` uses the same `ProgressFile` bridge but prints
panels to stdout instead of rendering alternate-screen UI.
