# protoagent_

<div align="center">
  <img src="misc/assets/banner.jpeg" alt="ProtoAgent banner" width="60%">
</div>

ProtoAgent is a local-first coding agent built around a fast Rust terminal UI
and a Python core powered by [ProtoLink](https://github.com/nMaroulis/protolink).
Its runtime is designed to give smaller models narrow roles, bounded evidence,
and deterministic completion checks instead of one large prompt with every
tool attached.

Release `0.3.0` covers the active `proto-cli` and `protoagent-core`
components. The ACP editor bridge remains a planned `0.0.0-dev.0` component.
See [VERSIONING.md](VERSIONING.md) and [CHANGELOG.md](CHANGELOG.md).

## Why ProtoAgent

- **Small-model first:** `small`, `medium`, `large`, and `api` prompt profiles
  tune delegation depth without changing the safety boundary.
- **Visible context:** Context Loom incrementally indexes the workspace and
  builds a bounded, source-cited Context Pack before inference.
- **Narrow agent roles:** Architect coordinates; Explorer reads the repository;
  Coder prepares policy-gated changes; Tester designs regression cases; Verifier runs approved checks; optional
  Scout researches the public web.
- **ProtoLink as the engine:** ProtoLink owns agent discovery, delegation,
  tools, state, events, approvals, cancellation, transports, and run reports.
- **Runtime completion checks:** ProtoAgent derives an application-level
  `RunContract` and does not treat unsupported prose as a completed write task.
- **Operator-visible behavior:** the Rust CLI exposes provider, model, context,
  agent, readiness, timeline, trace, diff, and session state.


## Architecture

| Surface | Status | Responsibility |
| --- | --- | --- |
| `cli/` | Active, `0.3.0` | Rust CLI/TUI, project and model controls, approvals, cancellation, and diagnostics. |
| `core/` | Active, `0.3.0` | Python application logic, Context Loom, prompt profiles, agent factories, and the ProtoLink runtime bridge. |
| `acp/` | Planned, `0.0.0-dev.0` | Future editor-facing Agent Client Protocol adapter. |

The default coding deck is intentionally asymmetric:

| Role | State | Authority |
| --- | --- | --- |
| **Architect** | Persistent project conversation | Plans, prepares bounded source packets, delegates, and explains results; no writes. |
| **Explorer** | Task-local | Reads and searches the selected workspace. |
| **Coder** | Task-local | Native recoverable file changes with write and restore approval. |
| **Tester** | Optional, default on; task-local | Read-only test planning and failure analysis. |
| **Verifier** | Tool-only, no memory | Native approved process execution and measured outcomes. |
| **Scout** | Tool-only, no memory, disabled by default | Exposes ProtoLink's bounded `web_search` and `fetch_url` tools with `network.read`. |
| **MCP** | Optional, default off; no model | Lazy discovery, one exact schema and approved allowlisted server calls. |

Scout is optional because external research changes the privacy and trust
boundary. Enable it only when a task needs current public information:

```bash
proto-cli agents scout on
```

Or inside the TUI:

```text
/agents scout on
```

The setting applies to the next run. When enabled, Scout is registered with the
same ProtoLink mesh, so Architect can discover it and call its tools. Search and
fetched text are treated as untrusted input; Scout has no workspace
tools. Brave search uses `BRAVE_SEARCH_API_KEY`; DuckDuckGo is keyless
best-effort search, and English Wikipedia is keyless factual search.

For the complete design, read the [whitepaper](whitepaper.md) and the
[maintainer documentation](https://nmaroulis.github.io/protoagent/).

Optional workers can be controlled from the shell or TUI. Architect, Explorer,
Coder and Verifier stay required. Disabling Tester removes its model and registry
card; Architect defines criteria and Coder adds regression tests while required
checks still run.

```bash
proto-cli agents tester off           # TUI: /agents tester off
proto-cli agents tester on
proto-cli mcp                        # Model-free setup/status
proto-cli mcp add docs ./docs-mcp.json # Import an explicit server/tool allowlist
proto-cli mcp test docs               # Discover only; no server tool invocation
proto-cli mcp on                      # TUI: /mcp on
```

The optional MCP broker uses ProtoLink 0.7.4's native adapter. Architect calls it
with `tool_call`, never `infer`. Three fixed broker tools keep large server
catalogs out of small-model prompts; connections and invocations use native
approvals. See the [MCP setup guide](docs/content/core/mcp.md) for local/remote
JSON examples, authentication and effect boundaries. Settings apply to the next run.

#### CLI Demo

Proto-CLI is the Rust terminal frontend for ProtoAgent. It renders the fullscreen TUI, project and model controls, approvals, cancellation, traces,
and session state while embedding the Python core through PyO3.

![cli_tui](https://raw.githubusercontent.com/nMaroulis/protoagent/refs/heads/main/misc/assets/cli/simple_task.gif)


## Install

Requirements:

- Python 3.12 or newer
- Rust toolchain with Cargo 1.85 or newer
- a supported local or API model provider

ProtoAgent 0.3.0 requires ProtoLink 0.7.4 or newer with the HTTP, LLM and MCP extras.

```bash
git clone https://github.com/nMaroulis/protoagent.git
cd protoagent

python3 -m venv .venv
source .venv/bin/activate
python -m pip install "protolink[http,llms,mcp]>=0.7.4"
python -m pip install -e core

cargo build --release --locked --manifest-path cli/Cargo.toml
```

Start the TUI from the repository root:

```bash
cargo run --locked --manifest-path cli/Cargo.toml -- project set /path/to/project
cargo run --locked --manifest-path cli/Cargo.toml -- start
```

Or run one task:

```bash
cargo run --locked --manifest-path cli/Cargo.toml -- run \
  "explain the authentication flow and propose a safer change"
```

Useful first checks:

```bash
cargo run --locked --manifest-path cli/Cargo.toml -- version
cargo run --locked --manifest-path cli/Cargo.toml -- check
cargo run --locked --manifest-path cli/Cargo.toml -- agents
```

Provider setup, every CLI command, Context Loom behavior, and troubleshooting
are covered in the [documentation](https://nmaroulis.github.io/protoagent/docs/intro).

## New in 0.3.0

- A runtime-owned TaskRecord preserves objectives, criteria, file scope, check IDs
  and worker outcomes independently of model conversation summaries.
- Read-only Tester designs regression cases; tool-only Verifier executes frozen
  repository checks. Arbitrary successful commands cannot verify a code change.
- Baseline checks and preparation permit later edits. Final checks bind source
  revisions and close the edit phase, with at most two bounded repairs.
- Coder reads bounded source spans and uses exact `edit_file` replacements with
  revision checks instead of regenerating whole files for small changes.
- Small profiles use compact protocol prompts and worker cards. Estimated
  request admission reserves output space and accounts for schemas, history and
  evidence; unknown small-model windows use an 8192-token application cap. Explicit model-size hints take priority over
  whether the model is served locally or remotely.
- `proto-cli eval coding --plan` shows disposable coding exercises;
  `--live --profile small` compares the deck with a single-agent baseline using
  the same model and independent acceptance tests.

Code changes need required final checks. Configure uncommon projects through
`.protoagent/project.json`; see the [task workflow guide](docs/content/core/task-workflow.md).
Documentation-only changes may finish with verification explicitly unverified.

## Safety And Privacy

Coder writes and restoration explicitly require native broker approval. Rust
displays the prepared diff and returns its exact request ID and fingerprint.
Esc or Ctrl-C requests `RunHandle` cancellation. Approval never counts as an
executed change; uncertain effects are not automatically replayed.

“Local-first” describes the default architecture, not a guarantee that every
configuration is offline. Local providers can keep model traffic on the
machine. API providers send model inputs to their configured endpoint, and
enabling Scout sends search/fetch requests to public internet services. Durable
ProtoLink trace output is opt-in through `PROTOAGENT_TRACE=1`.

## Project Status

The Rust CLI and Python core are the supported surfaces in `0.3.0`. The
[ACP directory](acp/README.md) is a roadmap placeholder; it is not currently an
installable editor server. Contributions should keep code, CLI help, readiness
output, docs, tests, and the changelog aligned.

## License

ProtoAgent is available under the [MIT License](LICENSE).
