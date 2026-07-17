# Proto-CLI

Proto-CLI is the Rust terminal frontend for ProtoAgent. It renders the
fullscreen TUI, project and model controls, approvals, cancellation, traces,
and session state while embedding the Python core through PyO3.

Current CLI version: `0.2.0`, sourced from `cli/Cargo.toml`.

Proto-CLI is a hybrid Rust/Python application, not a standalone binary: the
Python environment must contain `protoagent-core` and ProtoLink. Building the
CLI requires Rust/Cargo 1.83 or newer.

## Install

From the monorepo root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install "protolink[http,llms]>=0.6.6"
python -m pip install -e core
cargo build --release --locked --manifest-path cli/Cargo.toml
```

The built binary is `cli/target/release/proto-cli`.

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

## Agent Controls

The default runtime has a stateful Architect and task-local Explorer/Coder
workers. Scout is an optional task-local web research worker and is off by
default.

```bash
proto-cli agents
proto-cli agents profile small
proto-cli agents scout on
proto-cli agents scout off
```

The same controls are available in the TUI:

```text
/agents
/agents profile small
/agents scout on
/agents scout off
```

Agent-setting changes apply to the next run. When Scout is enabled, the Python
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
| `/agents` | Show the agent manifest, prompt profile, and optional Scout state. |
| `/agents profile [auto\|small\|medium\|large\|api]` | Show or set the prompt profile. |
| `/agents scout [on\|off]` | Enable or disable Scout for subsequent runs. |
| `/context [QUERY]` | Show Context Loom status or build a source-cited Context Pack. |
| `/context on`, `/context off` | Enable or disable persistent project conversation memory. |
| `/context history` | Inspect ProtoLink-owned Architect memory. |
| `/context compact [recent\|tokens\|summary] [limit]` | Compact saved history. |
| `/context reset` | Clear project conversation history and trim the Rust session index. |
| `/context window 16k` | Set the Ollama request window and ProtoLink model profile together. |
| `/index refresh` | Refresh the incremental Context Loom index. |
| `/trace`, `/timeline`, `/diff` | Inspect the latest normalized run trace, event sequence, or diff preview. |
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
