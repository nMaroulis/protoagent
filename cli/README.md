# Proto-CLI

Proto-CLI is the Rust terminal frontend for ProtoAgent. It renders the
fullscreen TUI, project and model controls, approvals, cancellation, traces,
and session state while embedding the Python core through PyO3.

Current CLI version: `0.3.0`, sourced from `cli/Cargo.toml`.

Proto-CLI is a hybrid Rust/Python application, not a standalone binary: the
Python environment must contain `protoagent-core` and ProtoLink. Building the
CLI requires Rust/Cargo 1.85 or newer.

## Install

From the monorepo root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install "protolink[http,llms,mcp]>=0.8.0"
python -m pip install -e core
cargo build --release --locked --manifest-path cli/Cargo.toml
```

The built binary is `cli/target/release/proto-cli`.

Development and test builds omit debug symbols and incremental compilation
caches to reduce disk usage. Dependencies remain cached, but recompiling changed
code may take longer. For source debugging, set `CARGO_PROFILE_DEV_DEBUG=2` when
building. Generated files in `cli/target/` are disposable; this command removes
debug build output while keeping the release executable:

```bash
cargo clean --manifest-path cli/Cargo.toml --profile dev
```

## Use

Select a workspace, then start the TUI:

```bash
proto-cli project set ~/projects/my-app
proto-cli start
```

Run a single task without opening the TUI:

```bash
proto-cli run "Explain the authentication flow and propose a safer diff"
```

Use `@` in the TUI editor to attach bounded read-only file context:

```text
explain @src/auth.rs and suggest a safer JWT flow
```

## Compose, Verify, Recover

Enter submits the complete prompt; Ctrl-J inserts a newline. Bracketed paste
retains multiline text without submitting. Tab opens slash-command completion
and Ctrl-R searches recent input. Ctrl-P/Ctrl-N browse history from any row.

Answers stream under `AGENT / architect` or `AGENT / guide`, with animated
thinking dots and a steady mint cursor on the text background. Bold, italic,
headings and highlighted inline/fenced code render as the answer streams,
preserving the original Markdown in saved responses. Activity stays in the bottom bar. Input hints
are a dim placeholder; the empty cursor blinks slowly. `/debug on` reveals
metadata below completed answers and a `/trace` hint; `/debug off` hides it.
This display setting defaults off for each TUI session.

Architect can ask a question during the same task through ProtoLink's native
`ask_user` tool. Type an answer or press Tab to use a suggestion, then Enter to
send. Esc skips; Ctrl-C cancels. PageUp/PageDown scroll long TUI questions.
Questions close on native timeout or budget expiry; feedback never authorizes
an action. Interactive shell runs support the same answer controls; redirected
input/output declines questions. This is live continuation, not restart/resume.

Two muted horizontal borders frame the input. It expands upward for multiline
text while the lower border and status stay anchored. Cached message layouts
and changed-row painting keep streaming work independent of old answer sizes
on animation-only ticks.

The header, composer and footer are cached independently. Unchanged frames write
nothing; input and progress are checked every 32 ms, with 120 ms animation steps.
Mouse-wheel/PageUp scrolling, resize and Ctrl-L redraw work during replies.
Pickers use buffered modal frames and keep their background between keystrokes.
Small windows show a resize hint and preserve the conversation and draft.

No LLM is needed to open the TUI, use static `/help`, change settings or toggle
agents. Coding requests and `/help QUESTION` need the selected model. An offline
local server produces an inline setup message before indexing; provider and JSON
errors leave the interface open. For Ollama, start `ollama serve` and select an
installed model. `/config` shows its URL, `/model` changes the selection and
`/trace` shows bounded, redacted diagnostics. Pre-bound SDK console loggers are
captured so their tracebacks cannot scroll away the terminal UI.

Model discovery and `/check` run in background workers; Esc/Ctrl-C dismisses
their read-only results. Discovery uses parallel metadata probes and never
constructs an LLM merely to open the model picker.

Real terminal regression tests use isolated configuration and local mock models:

```bash
cargo build --release --locked --manifest-path cli/Cargo.toml
.venv/bin/python cli/tests/tui_smoke.py
```

`/help QUESTION` and `proto-cli help "QUESTION"` stream Guide help using the
active model and bundled command reference, without requiring a project.

Architect delegates checks to Verifier through ProtoLink. Each command requires
approval of its argv, working directory and timeout; V opens the full preview.
Commands run with host access, may write files or use the network, and have
bounded output and a timeout. Final answers report measured outcomes.

Use `/checkpoints` and `/undo [id]` (or `proto-cli checkpoints` / `proto-cli undo`)
to review and restore individual Coder writes without a model. Undo requires
approval and refuses files edited after the agent write. Commands' side effects
are not checkpointed. See [Verify & Recover](../docs/content/cli/verification-and-recovery.md).

Commands run on the host with an explicit environment; they do not inherit the
CLI environment. Native file recovery requires POSIX and existing parent
directories. Pre-0.2.2 checkpoints remain available for inspection, while new
changes use ProtoLink's revision-aware restoration.

## Agent Controls

Architect, Explorer, Coder and the tool-only Verifier are required. Tester is an
optional test-design worker and defaults on. Scout web research and the tool-only
MCP broker default off. `/agents` shows all three optional workers and their
toggle commands.

```bash
proto-cli agents
proto-cli agents profile small
proto-cli agents tester off
proto-cli agents scout on
proto-cli agents mcp on
```

The same controls are available in the TUI:

```text
/agents
/agents profile small
/agents tester off
/agents scout on
/agents mcp on
```

Use `on` or `off` for any optional worker. Settings persist user-wide and apply
to the next run; they do not change a running task. Turning Tester off removes
its inference while required verification remains enforced. `/mcp` sets up the
broker's servers. Guide knows these controls: `/help how do I disable Tester?`
returns instructions and current settings without changing them.

When Scout is enabled, the Python
core registers ProtoLink's `web_search` and `fetch_url` tools with
`network.read`; Scout has no workspace-write capability.

## TUI Commands

| Command | Purpose |
| --- | --- |
| `/help` or `/help QUESTION` | Show command help or ask the isolated Guide agent a usage question. |
| `/dashboard` | Pin the runtime dashboard. |
| `/project [PATH\|clear]` | Inspect, select, or clear the active workspace. |
| `/models`, `/model`, `/key` | Inspect providers and configure a model or API key. |
| `/config` | Show redacted configuration. |
| `/check` | Refresh Python, ProtoLink, web-tool, transport, auth, and provider readiness. |
| `/version` | Show CLI, core, and planned ACP versions. |
| `/agents` | Show required roles, prompt profile, and optional Tester/Scout/MCP states. |
| `/agents profile [auto\|small\|medium\|large\|api]` | Show or set the prompt profile. |
| `/agents scout [on\|off]` | Enable or disable Scout for subsequent runs. |
| `/agents tester [on\|off]` | Enable or disable test-design inference; required checks remain enforced. |
| `/agents mcp [on\|off]` | Enable or disable the external-tool broker; use `/mcp` to set up servers. |
| `/context [QUERY]` | Show Context Loom status or build a source-cited Context Pack. |
| `/context on`, `/context off` | Enable or disable persistent project conversation memory. |
| `/context history` | Inspect ProtoLink-owned Architect memory. |
| `/context compact [recent\|tokens\|summary] [limit]` | Compact saved history. |
| `/context reset` | Clear project conversation history and trim the Rust session index. |
| `/context window 16k` | Set the Ollama request window and ProtoLink model profile together. |
| `/index refresh` | Refresh the incremental Context Loom index. |
| `/trace`, `/timeline`, `/diff` | Inspect the latest normalized run trace, event sequence, or diff preview. |
| `/debug [on\|off]` | Show the mode, reveal response metadata with a `/trace` hint, or hide it. |
| `/last` | Replay the last agent response. |
| `/run TASK` | Run a task from a slash command. |
| `/clear` | Clear the visible transcript. |

`/project`, `/models`, `/agents`, `/context`, `/check`, `/config`, `/help`, and
`/dashboard` update the fixed status panel instead of appending large status
blocks to the chat.

## Direct Commands

```bash
proto-cli start
proto-cli tui
proto-cli project
proto-cli project set ~/projects/my-app
proto-cli project clear
proto-cli run "Refactor the auth module"
proto-cli dashboard
proto-cli models
proto-cli model
proto-cli key openai
proto-cli config
proto-cli version
proto-cli check
proto-cli agents
proto-cli agents profile api
proto-cli agents scout on
proto-cli eval profiles --limit 3
proto-cli context
proto-cli context "runtime streaming task handling"
proto-cli index refresh
```

When running from the repository without installing the binary, prefix the
arguments with:

```bash
cargo run --locked --manifest-path cli/Cargo.toml --
```

## Safety And Network Boundaries

Workspace writes pause at ProtoLink's policy boundary and open an approval modal
containing the action's diff artifact. Esc or Ctrl-C during a run requests live
task cancellation.

Privacy depends on configuration. A local provider with Scout disabled can
keep model and research traffic local. API providers send model inputs to their
configured endpoints. Enabling Scout permits outbound public search and URL
fetches; returned content is untrusted and does not grant write authority.

The Python orchestration logic is documented in the
[core README](../core/README.md). The full command and TUI manual is in the
[documentation site](https://nmaroulis.github.io/protoagent/docs/cli/overview).

## v0.3.0 task workflow and coding evals

The deck adds a read-only Tester, runtime task state, bounded Coder reads and exact
edits. `run_check` selects a frozen project check by ID; baselines and preparation
allow subsequent editing, while final verification closes the edit phase. Code
changes without qualifying final checks remain incomplete. Configure unusual
checks in `.protoagent/project.json`; see the
[task workflow guide](../docs/content/core/task-workflow.md).

```bash
proto-cli eval coding --plan --json
proto-cli eval coding --live --profile small --task empty-average --json
```

The coding harness compares the deck and a single agent using the same selected
model on disposable fixtures. Live mode uses narrow fixture approvals and
independent acceptance tests. Generated code executes on the host without sandbox
isolation. Routing diagnostics remain available through `eval profiles`.


## Optional workers and MCP in v0.3.0

Tester defaults on, while Scout/MCP default off. `proto-cli agents tester|scout|mcp
on|off` and the matching `/agents` commands persist optional-worker settings for
the next run. Disabled workers are not constructed or registered. Architect,
Explorer, Coder and Verifier remain required; without Tester, Architect defines
criteria and Coder writes regressions while all selected checks still run.

`proto-cli mcp` (TUI `/mcp`) shows model-free setup/status. Import an explicit
server contract with `mcp add NAME FILE.json`, inspect with `mcp test NAME` or
`mcp tools NAME TOOL`, and enable with `mcp on`. Probes only discover; they do
not invoke server tools. The broker exposes three fixed tools for names, one
schema and an approved allowlisted invocation. Architect uses `tool_call`,
never another inference loop. ProtoLink 0.8.0 owns transports, schema validation,
session cleanup and result/error normalization. See the
[MCP guide](../docs/content/core/mcp.md) for local/remote setup and boundaries.

## Engine integration

The harness uses ProtoLink 0.8 owned local children and native context policies.
Run `proto-cli eval harness --json` for offline engine-contract checks. Optional
provider `fallback_models` are configured explicitly; restart/resume and Docker
execution are outside the current coding workflow integration. See the
[engine integration guide](../docs/content/core/protolink-migration.md) and [model configuration guide](../docs/content/core/config-models.md).
