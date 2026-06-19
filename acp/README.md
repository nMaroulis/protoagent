# Proto-ACP Server

**The native Agent Client Protocol (ACP) bridge for the ProtoAgent ecosystem.**

**Proto-ACP** is the editor-facing component of the ecosystem. It connects local LLMs (via Ollama or LM Studio) directly to your favorite code editor (Zed, JetBrains) using the Agent Client Protocol (ACP), while seamlessly routing external tools via the Model Context Protocol (MCP).

---

## 🏗️ Monorepo Context

This project is the editor integration frontend of the **ProtoAgent Ecosystem**.

```text
📂 protoagent/           <-- You are in the monorepo root
 ┣ 📂 core/              <-- The 'protolink' AI multi-agent orchestration logic (🐍 Python)
 ┣ 📂 cli/               <-- The standalone terminal UI (🦀 Rust)
 ┗ 📂 acp/               <-- YOU ARE HERE: The ACP server for Zed/JetBrains (🐍 Python)

```

### 🧠 Why an ACP Server specifically for Local AI?

Most powerful ACP agents in the registry (like Cline or Claude Agent) are heavily optimized for expensive, cloud-based frontier models. If you plug a local 8B model into them, their orchestration logic often breaks down because the models cannot handle the massive XML-heavy system prompts.

Proto-ACP acts as an intelligent buffer. Instead of passing complex tool-calling prompts directly to the local model, the editor talks to Proto-ACP. Proto-ACP then wakes up the `core/` orchestration engine (powered by `protolink`), which guides your smaller open-source models (like `qwen2.5-coder`) through specialized Multi-Agent loops (Planner ➔ Coder ➔ Critic) in the background. Once the logic is sound, Proto-ACP returns a perfectly clean file diff back to your editor.

### ✨ Key Features

* **🔒 100% Local Privacy:** Your code never leaves your machine. Full native support for Ollama and LM Studio APIs.
* **🔌 Universal IDE Support:** Plugs directly into Zed, IntelliJ, and PyCharm via the ACP standard. Zero custom extensions required.
* **👥 Multi-Agent Orchestration:** Automatically routes tasks between specialized internal personas living in the `core/` directory to prevent context-window explosions.
* **🛠️ MCP Native:** Automatically detects and ingests Model Context Protocol (MCP) servers configured in your editor, passing database, Git, and web-scraping tools directly to your local models.

---

## 🚀 Installation & Setup

Because Proto-ACP relies on the central `protoagent-core` engine, you run it directly from the monorepo's virtual environment.

### 1. Initialize the Core Environment

If you haven't already, set up the Python environment from the root of the repository:

```bash
cd ..
python3 -m venv .venv
source .venv/bin/activate
pip install "protolink[http,llms]>=0.6.1" pydantic

```

### 2. Connect Your Editor (Zed)

Proto-ACP works beautifully inside Zed's native AI Agent panel.

1. Open Zed Settings (`Cmd + ,` or `Ctrl + ,`).
2. Add the Proto-ACP server to your configuration, pointing it to the virtual environment we just created:

```json
{
  "agent": {
    "context_servers": {
      "protoagent": {
        "command": "${workspace_root}/.venv/bin/python",
        "args": ["${workspace_root}/acp/server.py"]
      }
    }
  }
}

```

*(Note: Replace `${workspace_root}` with the absolute path to your cloned `protoagent` folder if Zed requires strict absolute paths).*

3. Open the Agent Panel (`Cmd + N`), select **ProtoAgent** from the dropdown, and start coding locally!

### 3. JetBrains (IntelliJ, PyCharm)

*Integration documentation for the JetBrains ACP plugin is coming soon.*

---

## ⚙️ Configuration (Ollama / LM Studio)

By default, the core engine routes all inference to `http://localhost:11434/v1` (Ollama's default port). To customize the provider or model, initialize the config via the CLI or manually create `~/.protoagent/config.json`:

```json
{
  "provider": "ollama",
  "api_base": "http://localhost:11434/v1",
  "default_model": "qwen2.5-coder:7b",
  "max_context_tokens": 32000
}

```

---

## 🧩 Extending with MCP Tools

Proto-ACP is designed to be the ultimate local router. If you configure MCP tools in your editor (like a PostgreSQL connector or GitHub integration), the server will automatically detect the incoming JSON Schemas and pass them down into the `core/` engine to be injected into the sub-agents' system prompts.

You do not need to write custom Python code to add new tools to ProtoAgent—just plug them into your editor!

---

## 🛣️ Roadmap

* [ ] Core ACP `stdio` server implementation
* [ ] Integration with `protolink` routing logic inside `core/`
* [ ] Local LLM standard API support (Ollama / LM Studio)
* [ ] Dynamic MCP tool injection mapping
* [ ] Submit to the official ACP Registry
* [ ] Publish the architecture White Paper

---

## 🤝 Contributing

We want ProtoAgent to be the default open-source orchestration layer for local development.

If you want to optimize the `protolink` prompts for smaller models, add new sub-agent personas, or fix ACP wire-compatibility bugs, please check the contribution guidelines in the root `README.md`.

---

## 📄 License

Distributed under the MIT License. See `LICENSE` for more information.

*Built with ❤️ by Nikolaos Maroulis (@nMaroulis) using the protolink library.*
