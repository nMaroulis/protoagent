# ProtoAgent Core

Python brain for the ProtoAgent frontends. The Rust CLI imports this package
through PyO3 and expects JSON strings from `protoagent_core.agent_engine`.

Install ProtoLink with the HTTP transport and LLM extras so the embedded
Agent runtime can import and create provider clients:

```bash
pip install "protolink[http,llms]"
```

## Layout

- `protoagent_core/agent_engine.py` - PyO3-facing functions for prompts, model discovery, config, doctor checks, and approved action application.
- `protoagent_core/runtime.py` - Embedded ProtoLink mesh runner. It starts a local Registry, registers the agent deck, and sends user tasks to Architect with `AgentClient`.
- `protoagent_core/models.py` - Ollama, LM Studio, llama.cpp, and API model inventory.
- `protoagent_core/config.py` - Provider config and API-key storage at `~/.protoagent/config.json`.
- `protoagent_core/agents/` - ProtoLink Architect, Explorer, and Coder factories, split by agent.
- `protoagent_core/tools.py` - Read-only exploration tools plus diff/new-file proposal tools.

## Provider Execution

The CLI invokes the selected provider/model through ProtoLink agents by
default. The selected model is used to create fresh LLM instances for the
Architect, Explorer, and Coder on each run. Use scaffold mode only when you
want to test the Rust/Python contract without contacting a model:

```bash
PROTOAGENT_SCAFFOLD=1 cargo run --manifest-path cli/Cargo.toml -- run "your task"
```

The full ProtoLink A2A mesh factories are in `protoagent_core/agents/`.
The embedded CLI runtime uses ProtoLink's Registry and `AgentClient`, so
Architect discovers Explorer and Coder through the registry and delegates with
ProtoLink `agent_call` semantics.
