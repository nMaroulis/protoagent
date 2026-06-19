# ProtoAgent Core

Python brain for the ProtoAgent frontends. The Rust CLI imports this package
through PyO3 and expects JSON strings from `protoagent_core.agent_engine`.

Install ProtoLink 0.6.1 or newer with the HTTP/SSE transport and LLM extras so
the embedded Agent runtime can import streaming agents, lifecycle-aware task
status events, and provider clients:

```bash
pip install "protolink[http,llms]>=0.6.1"
```

## Layout

- `protoagent_core/agent_engine.py` - PyO3-facing functions for prompts, model discovery, config, and doctor checks.
- `protoagent_core/runtime.py` - Embedded ProtoLink mesh runner. It attaches `RunContext`, normalizes `RunEvent`s, and sends tasks to Architect with `AgentClient`.
- `protoagent_core/runtime_bridge.py` - Application approval and cancellation bridge for the Rust CLI.
- `protoagent_core/models.py` - Ollama, LM Studio, OpenAI-compatible, llama.cpp, and API model inventory.
- `protoagent_core/config.py` - Provider config and API-key storage at `~/.protoagent/config.json`.
- `protoagent_core/context/` - Context Loom indexer, SQLite store, and source-cited Context Pack builder.
- `protoagent_core/agents/` - ProtoLink Architect, Explorer, and Coder factories, split by agent.
- `protoagent_core/tools.py` - Workspace-safe exploration, diff preview, and authorized write helpers.

## Provider Execution

The CLI invokes the selected provider/model through ProtoLink agents by
default. The selected model is used to create fresh LLM instances for the
Architect, Explorer, and Coder on each run. Agents use ProtoLink's SSE
JSON-RPC lifecycle-aware task stream by default, while the Registry remains on
plain HTTP.

Before each model run, Context Loom refreshes a deterministic local index and
injects a bounded Context Pack into the Architect prompt. Explorer also exposes
`build_context_pack` as a ProtoLink tool so the agent mesh can ask for a
focused evidence pack during a run.

Useful runtime switches:

- `PROTOAGENT_STREAM=0` disables stream consumption and uses request/response.
- `PROTOAGENT_AGENT_TRANSPORT=http` forces the older HTTP-only agent mesh.
- `PROTOAGENT_STREAM_TRACE_LIMIT=120` controls how many stream summaries are retained for the Rust UI.

Use scaffold mode only when you want to test the Rust/Python contract without contacting a model:

```bash
PROTOAGENT_SCAFFOLD=1 cargo run --manifest-path cli/Cargo.toml -- run "your task"
```

The full ProtoLink A2A mesh factories are in `protoagent_core/agents/`.
The embedded CLI runtime uses ProtoLink's Registry and `AgentClient`, so
Architect discovers Explorer and Coder through the registry and delegates with
ProtoLink `agent_call` semantics.

Coder tools declare `workspace.write` and build `RunAction` objects with
`Artifact(kind="preview", media_type="text/x-diff")`. Protolink policy pauses
those actions and calls the Rust-owned approval handler before execution. The
same control bridge forwards TUI cancellation through `AgentClient.cancel_task()`.
