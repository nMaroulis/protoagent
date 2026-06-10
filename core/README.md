# ProtoAgent Core

Python brain for the ProtoAgent frontends. The Rust CLI imports this package
through PyO3 and expects JSON strings from `protoagent_core.agent_engine`.

## Layout

- `protoagent_core/agent_engine.py` - PyO3-facing functions for prompts, model discovery, config, doctor checks, and approved action application.
- `protoagent_core/models.py` - Ollama, LM Studio, llama.cpp, and API model inventory.
- `protoagent_core/config.py` - Provider config and API-key storage at `~/.protoagent/config.json`.
- `protoagent_core/agents.py` - Protolink Architect, Explorer, and Coder factories.
- `protoagent_core/tools.py` - Read-only exploration tools plus diff/new-file proposal tools.

## Provider Execution

The CLI calls the selected provider/model by default. Use scaffold mode only
when you want to test the Rust/Python contract without contacting a model:

```bash
PROTOAGENT_SCAFFOLD=1 cargo run --manifest-path cli/Cargo.toml -- run "your task"
```

The full protolink A2A mesh factories are in `protoagent_core/agents.py`.
The immediate CLI runtime uses direct provider HTTP calls so a selected model
responds right away.
