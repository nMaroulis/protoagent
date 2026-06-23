# Proto-CLI

**The blazing-fast terminal interface for the ProtoAgent ecosystem. Built in Rust, powered by Python.**

**Proto-CLI** is a hackable, autonomous AI coding assistant designed specifically for terminal developers utilizing local inference (Ollama, LM Studio).

Instead of forcing you to choose between a fast terminal experience and intelligent multi-agent routing, Proto-CLI gives you both. By utilizing a hybrid architecture via PyO3, it wraps the powerful Python-based orchestration engine into a standalone, lightning-fast Rust binary.

---

## 🏗️ Monorepo Context

This project is the terminal frontend of the **ProtoAgent Ecosystem**.

```text
📂 protoagent/           <-- You are in the monorepo root
 ┣ 📂 core/              <-- The 'protolink' AI multi-agent orchestration logic (🐍 Python)
 ┣ 📂 cli/               <-- YOU ARE HERE: The standalone terminal UI (🦀 Rust)
 ┗ 📂 acp/               <-- The ACP server for Zed/JetBrains integration (🐍 Python)

```

### ✨ Key Features

* **🦀 Zero-Overhead UI:** Written purely in Rust. Enjoy snappy rendering, smooth spinners, and instant boot times without the bloat of standard Python CLI libraries.
* **🐍 Python Hackability:** All the actual AI logic (prompts, tool routing, sub-agents) lives next door in the `core/` directory. If you want to tweak how the Planner or Coder agents think, you edit simple Python scripts while keeping your fast Rust terminal.
* **🔒 100% Local Privacy:** Native, first-class support for OpenAI-compatible local endpoints (`http://localhost:11434/v1`). Your code never leaves your machine.
* **🛡️ Human-in-the-Loop:** Automatically halts and renders a clean diff, requiring an explicit `[Y/n]` approval before executing shell commands or writing files to disk.

---

## 🚀 Installation & Build

Because Proto-CLI embeds the `protolink` Python engine, you need to ensure the Python virtual environment is active so Rust can bind to it during the build process.

### 1. Setup the Core Engine

If you haven't already, initialize the Python environment in the monorepo root:

```bash
cd ..
python3 -m venv .venv
source .venv/bin/activate
pip install "protolink[http,llms]>=0.6.2"

```

### 2. Build the CLI

Navigate back to the `cli` directory and build the Rust project via Cargo:

```bash
cd cli
cargo build --release

```

You can now link the compiled binary to your path:

```bash
ln -s $(pwd)/target/release/proto-cli /usr/local/bin/protoagent

```

*(Note: Pre-compiled binaries requiring zero setup will be available on the Releases page soon).*

---

## 💻 Usage

Choose the project folder first. ProtoAgent remembers the last opened project
and reopens it on the next start.

```bash
# Choose the folder the agent should work inside
protoagent project set ~/projects/my-app

# Start an interactive autonomous session
protoagent start

# Explicit terminal UI alias
protoagent tui

# Or pass a direct task
protoagent run "Refactor the authentication logic in src/auth.rs to use JWTs"

```

Inside the TUI, type a normal sentence to run a task. Use `@` in the input to
open the project file picker and insert a tagged file reference, for example:

```text
explain @src/auth.rs and suggest a safer JWT flow
```

Tagged files are loaded as bounded read-only context before the agent pipeline
runs.

Workspace writes pause inside Protolink's policy boundary and open a typed
approval modal with the action's diff artifact. Press Esc or Ctrl-C while a
task is running to request live task cancellation.

### TUI Commands

`protoagent start` opens the fullscreen Rust terminal UI. The TUI takes over the
terminal with a fixed status panel, a dedicated chat viewport, and a bottom input
editor. Scrolling is handled inside the chat, not by shell scrollback.

Inside the TUI you can use slash commands:

* `/clear` - Clears the chat transcript.
* `/dashboard` - Shows the dashboard status panel.
* `/project` - Choose the active project folder.
* `/project PATH` - Open a project folder directly.
* `/project clear` - Clear the active project folder.
* `/models` - Shows model/provider status.
* `/config` - Shows redacted provider config status.
* `/check` - Refreshes Python, protolink, and active provider status.
* `/agents` - Shows the Architect / Explorer / Coder topology.
* `/context` - Shows Context Loom index status for the active project.
* `/context QUERY` - Builds a source-cited Context Pack without running a model.
* `/context window 16k` - Sets the Ollama request window and ProtoLink `LLMModelProfile` together.
* `/context compact [recent|tokens|summary] [limit]` - Compacts the active per-agent ProtoLink session.
* `/context reset` - Clears the active ProtoLink histories and the Rust session index.
* `/index refresh` - Refreshes the local Context Loom SQLite index.
* `/last` - Replays the last agent response.
* `/run` - Runs a task from a slash command.
* `/help`  - Shows available commands in the fixed panel.

### Direct Commands

```bash
cargo run --manifest-path cli/Cargo.toml -- start
cargo run --manifest-path cli/Cargo.toml -- tui
cargo run --manifest-path cli/Cargo.toml -- cli
cargo run --manifest-path cli/Cargo.toml -- project
cargo run --manifest-path cli/Cargo.toml -- project set ~/projects/my-app
cargo run --manifest-path cli/Cargo.toml -- project clear
cargo run --manifest-path cli/Cargo.toml -- run "Refactor the auth module"
cargo run --manifest-path cli/Cargo.toml -- dashboard
cargo run --manifest-path cli/Cargo.toml -- models
cargo run --manifest-path cli/Cargo.toml -- model
cargo run --manifest-path cli/Cargo.toml -- key openai
cargo run --manifest-path cli/Cargo.toml -- config
cargo run --manifest-path cli/Cargo.toml -- check
cargo run --manifest-path cli/Cargo.toml -- agents
cargo run --manifest-path cli/Cargo.toml -- context
cargo run --manifest-path cli/Cargo.toml -- context "runtime streaming task handling"
cargo run --manifest-path cli/Cargo.toml -- index refresh
```

Interactive mode is the fullscreen TUI by default. The top area is a fixed
status panel, the middle viewport is the chat transcript, and the bottom bar is
the input editor. `/project`, `/models`, `/agents`, `/context`, `/check`,
`/config`, `/help`, and `/dashboard` switch the fixed status panel instead of
printing status blocks into the chat.

---

## 🧠 Configuration (Ollama / LM Studio)

By default, ProtoAgent routes all inference to your local Ollama instance. To point it to a different model or LM Studio, generate a config file:

```bash
protoagent config init

```

Edit `~/.protoagent/config.json`:

```json
{
  "provider": "ollama",
  "api_base": "http://localhost:11434/v1",
  "default_model": "qwen2.5-coder:7b",
  "max_context_tokens": 32000
}

```

---

## 🤝 Contributing to the CLI

We welcome Rustaceans 🦀 to help us optimize this frontend!

Areas we are actively looking to improve:

* Enhancing the `clap` terminal argument parsing.
* Surfacing ProtoLink SSE task events as live Rust UI updates instead of post-run trace summaries.
* Adding beautiful, bat-like terminal diffing for the `[Y/n]` file write approvals.

To contribute to the underlying agent logic or prompts, head over to the [`core/`](https://www.google.com/search?q=../core/README.md) directory.
