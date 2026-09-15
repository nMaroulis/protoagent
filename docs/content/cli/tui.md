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
| Input area | 6 rows | Three prompt rows, command hints, context meter, project/chat/activity status. |

The shell scrollback is not used during fullscreen mode. The transcript owns
its own scroll offset.

Response metadata and detail labels appear directly beneath the answer in
faint, light-gray text, aligned with the answer. This uses standard terminal
styling, with gray as the fallback when faint text is unsupported. The answer
keeps its existing formatting; metadata uses the terminal's normal font size.

## Panels

The pinned panel is a compact status dashboard at the top of the screen.

| Panel | Shows |
| --- | --- |
| Dashboard | Project, model inventory summary, agent roles, last query, UI mode. |
| Project | Active project, state, `/project` commands, file-tagging hint, project config path. |
| Models | Active provider/model, inventory status, provider chips, setup commands, config path. |
| Agents | Architect, Context Loom, Explorer, Coder, Verifier, optional Scout ON/OFF, and approval boundary. |
| Context | Context Loom, context window, memory commands, pack preview, index refresh. |
| Sessions | Current session, store path, recent sessions. |
| Timeline | Latest structured agent path. |
| Check | Runtime readiness, active provider, workspace, config. |
| Config | Provider/model/config/key hints. |
| Versions | CLI, Python core, and planned ACP version inventory. |
| Help | Command map, scrolling, cancellation, launch forms. |

The command bar exposes quick panel commands:

```text
/dashboard /project /models /agents /context /sessions /timeline /check /config /version /help
```

The Agents panel reads the same Python manifest used by the shell command. Its
Scout row shows whether the optional `web_search`/`fetch_url` worker is
registered for the next run and gives the inverse toggle command:

```text
/agents scout on
/agents scout off
```

## Input Editor

The input editor wraps by terminal cell width and keeps the cursor visible in a
three-row composer. Emoji and combining characters are edited as grapheme clusters.

| Key | Behavior |
| --- | --- |
| Enter | Submit the complete prompt. |
| Ctrl-J | Insert a newline. |
| Shift-Enter / Alt-Enter | Insert a newline when the terminal reports the modifier. |
| Left / Right | Move cursor. |
| Home / End | Move to start/end of the logical line. |
| Backspace / Delete | Delete complete grapheme clusters. |
| Up / Down | Move between wrapped rows; browse history for a single-row prompt. |
| Ctrl-P / Ctrl-N | Browse history from any row, preserving the unsubmitted draft. |
| Ctrl-R | Search recent prompts and recall a selection without submitting. |
| Tab | Open fuzzy slash-command completion; otherwise insert two spaces. |
| `@` | Open the project file picker when a project is active. |

Bracketed paste keeps multiline text and `@tags` literal: it neither submits the
prompt nor opens a picker. Control bytes are removed; tabs become two spaces.

Input history is stored in memory for the current TUI process and capped by
`INPUT_HISTORY_CAPACITY`.

## File Tag Picker

Typing `@` opens a searchable modal backed by
`cli/src/terminal_ui/project.rs`.

The picker:

1. Scans the active project for taggable text files.
2. Skips hidden and ignored directories such as `.git`, `.venv`,
   `node_modules`, `target`, `dist`, and `build`.
3. Skips symlink entries and common binary suffixes.
4. Lets the user type to filter results.
5. Inserts either `@path` or `@"path with spaces"` into the prompt.

The Python core later resolves those tags with `safe_path()` and loads bounded
read-only context.

## Streaming answers

Replies use an `AGENT / architect` or `AGENT / guide` heading. Each starts with
a steady mint `_` cursor and animated `thinking.`, `thinking..`, `thinking...` dots.
As answer text arrives, it appears in that same message with a `streaming`
indicator. JSON action wrappers are hidden. Response text and its cursor retain
your terminal's default background. The cursor does not blink and reserves a
fixed cell through completion; the rest of the UI keeps its existing palette.

Answers render Markdown while they stream: **bold**, *italic*, headings,
yellow-highlighted inline code and mint fenced code blocks with preserved
indentation. Opening and closing markers become styles instead of raw tags;
partial delimiters are held at the live edge. Formatting crosses wrapped lines,
and Unicode display widths keep emoji and wide characters aligned. These are
presentation styles; the original Markdown stays in the saved answer.

Completion updates the status in that same header. The answer stays in place:
report footnotes and expanding trace blocks do not change its position. Terminal
frames use synchronized updates where supported to reduce flicker.

Unchanged messages reuse cached Markdown layouts. Streaming paints only changed
transcript rows; spinner ticks do not copy or reformat an unchanged answer.
Resize, debug changes and returning from a modal refresh the affected layout.

Worker activity appears only in the bottom status bar; the top model row shows
the provider and model. Use `/trace` for the full trace and
retained worker/command output, or `/diff` for proposed changes. Esc or Ctrl-C
cancels an active task or Guide answer.

`/help QUESTION` streams from Guide using the active model, without requiring
an active project. Guide receives a bundled command reference and a redacted
settings snapshot on each call. For example, `/help what does config do?`
can explain `/config`, `proto-cli config` and the commands for changing settings.

## Quiet composer and debug details

Keyboard hints are a faint placeholder inside the empty input. They disappear
when you type; slash-command suggestions sit in the lower border. Two muted
horizontal lines frame the composer, with a blank gap above it. The single-line
prompt sits near the bottom and expands upward to show up to three lines while
the lower border, context meter and status bar stay anchored. The empty input cursor
blinks slowly, with 700 ms per phase, without repainting the conversation.

`/debug on` shows the metadata previously displayed under answers: provider,
model, status, elapsed time, transport metrics, event/approval counts and report
labels, plus a `/trace` hint. It reveals metadata on existing answers too.
`/debug off` returns to the clean view; `/debug` reports the current mode.
Debug defaults off for each TUI session and does not change agent execution,
approvals or trace recording. With debug on, details appear after completion;
use the default off mode to keep the answer layout unchanged.

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
