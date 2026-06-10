# ProtoAgent Core

Python brain for the ProtoAgent frontends. The Rust CLI imports this package
through PyO3 and expects JSON strings from `protoagent_core.agent_engine`.

## Layout

- `protoagent_core/agent_engine.py` - PyO3-facing functions for prompts, model discovery, config, doctor checks, and approved action application.
- `protoagent_core/models.py` - Ollama, LM Studio, llama.cpp, and API model inventory.
- `protoagent_core/config.py` - Provider config and API-key storage at `~/.protoagent/config.json`.
- `protoagent_core/agents.py` - Protolink Architect, Explorer, and Coder factories.
- `protoagent_core/tools.py` - Read-only exploration tools plus diff/new-file proposal tools.

## Live Protolink Mode

The CLI is wired and safe by default. The live protolink execution path is
gated so provider/runtime debugging can happen separately:

```bash
PROTOAGENT_LIVE=1 cargo run --manifest-path cli/Cargo.toml -- run "your task"
```

The current live gate validates protolink and exposes the agent factories.
The next implementation step is launching the HTTP/runtime mesh and routing
the Architect through Explorer and Coder.
