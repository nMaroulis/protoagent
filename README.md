# ProtoAgent

<div align="center">
  <img src="https://github.com/nMaroulis/protoagent/blob/main/misc/assets/banner.jpeg?raw=true" alt="ProtoAgent Logo" width="60%">
</div>

**A local-first, experimental AI agent ecosystem engineered for speed, privacy, and absolute developer control.**

The core philosophy of ProtoAgent is simple: **Decouple the Brain from the Frontends.** Cloud subscriptions and bundled API plans are great for one-off chats, but when you start running complex, multi-agent autonomous loops, your quotas for Claude and Gemini go out pretty fast. Agentic coding requires hundreds of iterative steps, which means it fundamentally belongs on local compute. ProtoAgent is designed to give you that premium, autonomous coding assistant experience—entirely for free, running on your own hardware.

Current component versions: `proto-cli` `0.1.0`, `protoagent-core` `0.1.0`, and planned `proto-acp` `0.0.0-dev.0`. See [VERSIONING.md](VERSIONING.md) for the source-of-truth files and bump policy.

---

## 🧩 The Ecosystem Architecture

ProtoAgent isn't just a script; it's a unified, modular ecosystem. By keeping the heavy orchestration logic centralized, every time the core gets smarter, all of your development environments get the upgrade instantly.

### 1. The Core Engine (The Brain)

**Powered by `protolink` • Python**
If **LangChain** is the massive, heavy enterprise framework for cloud models, `protolink` is the lightning-fast, minimalist alternative engineered strictly building autonomous agents using a simple intuitive API, powered by the Agent-to-Agent (A2A) protocol.

* **Small-Model Optimized:** Cloud agents use massive system prompts filled with complex XML tags that instantly confuse smaller local models. ProtoAgent keeps a stateful Architect controller on top of stateless Explorer/Coder workers, so 7B/8B parameter models get narrow tasks, typed artifacts, and runtime completion checks instead of a giant prompt.
* **Prompt Profiles:** The agent deck can run in `auto`, `small`, `medium`, `large`, or `api` prompt modes, so local 7B models get terse deterministic instructions while frontier API models get richer planning and verification behavior.
* **Context Loom:** ProtoAgent builds a local, source-cited Context Pack from the active workspace before the agent deck runs. This gives smaller models a compact evidence map instead of a giant hidden repo dump.
* **ProtoLink-native context control:** Every model is configured with a typed `LLMModelProfile`; live context events drive the Rust meter, while persistent Architect history is described, compacted, and reset through ProtoLink state APIs. Explorer and Coder stay task-local.
* **ProtoLink-native transport operations:** Concrete 0.6.5 transports own payload/concurrency limits, health, lifecycle cleanup, and dependency-free metrics; the Rust CLI surfaces readiness and per-run counters without a parallel transport layer.
* **Agnostic:** It doesn't care what UI you are using. It receives prompts and emits lifecycle-aware task events plus typed action payloads.

Check out the Protoagent 📚 [**whitepaper**](https://github.com/nMaroulis/protoagent/whitepaper.md).

📝 Check out this article on **Level Up Coding** on **Medium**, for lessons learned while building this project: [Building My Own Local “Claude Code”: What I Learned Demystifying Agentic Coding under the Hood](https://levelup.gitconnected.com/building-my-own-local-claude-code-what-i-learned-demystifying-agentic-coding-under-the-hood-8772874b91b8)

### 2. The CLI (The Terminal Face)

**Embedded via PyO3 • Rust**
A blazing-fast, gorgeous terminal wrapper for developers who live in the shell.

* **The Experience:** Think of this as a purely local, offline version of **Claude Code** or **Clive**.
* **Zero Overhead:** Because it's written in Rust, it launches instantly. It intercepts the Python core's output to render smooth progress spinners and forces an interactive `[Y/n]` approval prompt before the agent is allowed to execute any bash commands or modify your files.


> The following animation shows a demo of a simple Task handled by the ProtoAgent CLI.

![cli_demo](https://raw.githubusercontent.com/nMaroulis/protoagent/refs/heads/main/misc/assets/cli/simple_task.gif)

### 3. The ACP Server (The Editor Face)

**Native Integration • Python/Rust**
A specialized server that implements the Agent Client Protocol (ACP) to bring the ProtoAgent core directly into your IDE.

* **The Experience:** Think of this as your local, ultra-reliable alternative to **Cline** or **Aider**.
* **Universal Support:** Hook it seamlessly into **Zed**, PyCharm, JetBrains, or any editor that supports modern agent protocols. Your editor handles the chat UI; ProtoAgent handles the heavy multi-agent routing in the background and hands back clean, perfectly formatted file diffs.

---

## 🚀 Getting Started

### Prerequisites

* Rust toolchain (Cargo 1.80+)
* Python 3.12+
* A local LLM runner (Ollama, LM Studio, etc.)

### 1. Install the Core Workspace

Clone the monorepo and initialize the isolated Python environment. This ensures the Protolink engine has everything it needs to run the background routing.

```bash
git clone https://github.com/yourusername/protoagent.git
cd protoagent

# Initialize the Brain
python3 -m venv .venv
source .venv/bin/activate
pip install "protolink[http,llms]>=0.6.5"
pip install -e core

```

### 2. Launch the Terminal Agent (CLI)

To spin up the high-performance Rust interface (make sure your `.venv` is active so Rust can embed the Python logic!):

```bash
cd cli
cargo run

```

Check component versions from the repo root:

```bash
cargo run --manifest-path cli/Cargo.toml -- version
```

### 3. Hook into Your Editor (Zed / JetBrains)

To pipe the local orchestration engine directly into your code editor via ACP, append the server path to your editor's configuration (e.g., `settings.json` in Zed):

```json
{
  "lsp": {
    "proto-acp": {
      "binary": {
        "path": "${workspace_root}/.venv/bin/python",
        "arguments": ["${workspace_root}/acp/server.py"]
      }
    }
  }
}

```

---

## 🛡️ Safety First: The Human-in-the-Loop Protocol

ProtoAgent strictly respects your system boundaries. Workspace writes are prepared as typed Protolink runtime actions before execution.

> **Immutable Rule:** Coder tools declare `workspace.write`. A `CapabilityPolicy` requires approval, the `RunAction` carries a `text/x-diff` preview artifact, and the Rust CLI returns a correlated `ApprovalDecision` before Protolink executes the write. During a running TUI task, Esc or Ctrl-C requests Protolink task cancellation.

Local doesn't just mean free; it means total control.
