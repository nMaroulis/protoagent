# Terminal UI Structure

This folder contains the fullscreen ProtoAgent terminal interface. The code is split by responsibility so each file has a narrow reason to change.

## Entry Flow

`terminal_ui.rs` is the coordinator:

- starts the TUI loop with `interactive()`
- dispatches slash commands
- runs agent tasks
- updates `TerminalApp` state
- delegates rendering, modal flows, approval, model picking, and project picking to focused modules

## Modules

| File | Responsibility |
| --- | --- |
| `surface.rs` | Terminal takeover, raw mode lifecycle, input loop, mouse/keyboard handling |
| `render.rs` | Fixed header, transcript, command bar, bottom input rendering |
| `state.rs` | App state, panels, messages, status snapshots, transcript helpers |
| `theme.rs` | Colors, clipping, terminal write helpers |
| `input.rs` | Single-line editor, cursor movement, input history |
| `modal.rs` | Shared modal primitives, text prompt, searchable picker |
| `project.rs` | `/project` flow, project folder selection, `@file` tagging |
| `model_picker.rs` | `/model` provider/model selection flow |
| `diff_view.rs` | Diff parsing, diff review modal, approval diff summary |
| `approval.rs` | Approval prompt and apply-action execution |

## Data Flow

1. `surface.rs` reads user input and returns a line to `terminal_ui.rs`.
2. `terminal_ui.rs` routes slash commands or task prompts.
3. Task prompts call the Python core through `call_process_prompt`.
4. Responses are stored in `TerminalApp` and rendered by `render.rs`.
5. File-changing responses go through `approval.rs` before writes are applied.

## Maintenance Rules

- Keep terminal drawing code in `render.rs`, `modal.rs`, `diff_view.rs`, or `theme.rs`.
- Keep state mutation on `TerminalApp` explicit and close to command/task handling.
- Keep provider/model logic in `model_picker.rs`.
- Keep project filesystem scanning and file tagging in `project.rs`.
- Do not add side effects outside `approval.rs` unless the command is explicitly meant to mutate local config or project state.
