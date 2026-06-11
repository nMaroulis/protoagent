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
pip install "protolink[http,llms]>=0.5.8"

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

Navigate to your project directory and wake up the agent. It will automatically detect your local Git context and file structure.

```bash
# Start an interactive autonomous session
protoagent start

# Or pass a direct task
protoagent run "Refactor the authentication logic in src/auth.rs to use JWTs"

```

### In-App Commands

Once the interactive terminal is running, you can use slash commands:

* `/clear` - Clears the current terminal buffer.
* `/menu` - Opens the command palette.
* `/dashboard` - Redraws the full cockpit.
* `/models` - Shows Ollama, LM Studio, llama.cpp, and cloud provider model options.
* `/model` - Selects the active provider/model.
* `/key` - Adds an OpenAI, Anthropic, Gemini, or DeepSeek API key.
* `/config` - Shows the redacted provider config.
* `/doctor` - Checks Python, protolink, and the active provider.
* `/agents` - Shows the Architect / Explorer / Coder topology.
* `/last` - Re-renders the last agent response.
* `/history` - Shows the retained prompt history for this session.
* `/diff` - Re-renders the last proposed diff.
* `/help`  - Displays all available commands.

### Direct Commands

```bash
cargo run --manifest-path cli/Cargo.toml -- start
cargo run --manifest-path cli/Cargo.toml -- run "Refactor the auth module"
cargo run --manifest-path cli/Cargo.toml -- dashboard
cargo run --manifest-path cli/Cargo.toml -- models
cargo run --manifest-path cli/Cargo.toml -- model
cargo run --manifest-path cli/Cargo.toml -- key openai
cargo run --manifest-path cli/Cargo.toml -- config
cargo run --manifest-path cli/Cargo.toml -- doctor
cargo run --manifest-path cli/Cargo.toml -- agents
```

Interactive mode runs inline by default so your terminal scrollback keeps the
agent transcript. The prompt has a visible cursor, basic editing keys, and
Up/Down navigation through the last 10,000 session inputs. Set
`PROTOAGENT_ALT_SCREEN=1` to run it in an alternate-screen cockpit.

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
