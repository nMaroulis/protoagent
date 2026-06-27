---
title: First Run
description: Start ProtoAgent, choose a project, select a model, and run a task.
---

The CLI requires an active project folder before it will run agent tasks. That
project folder becomes the workspace boundary for file reads, Context Loom
indexes, and approval-gated writes.

## 1. Choose A Project

```bash
cargo run --manifest-path cli/Cargo.toml -- project set ~/projects/my-app
```

Equivalent TUI flow:

```bash
cargo run --manifest-path cli/Cargo.toml -- start
```

Then run:

```text
/project
```

The active project is stored in `~/.protoagent/project.json` unless
`PROTOAGENT_CONFIG_DIR` is set.

## 2. Pick A Provider And Model

```bash
cargo run --manifest-path cli/Cargo.toml -- model
```

This opens an interactive provider and model picker. It reads the inventory from
the Python core and may prompt for API keys when a cloud provider is selected.

Inside the TUI:

```text
/model
```

## 3. Check Runtime Wiring

```bash
cargo run --manifest-path cli/Cargo.toml -- check
```

The check report verifies:

| Check | Source |
| --- | --- |
| Python version and platform | `agent_engine.doctor()` |
| ProtoLink install and runtime readiness | `llm.validate_protolink()` |
| Streaming support | `Agent.handle_task_streaming` and `AgentClient.send_task_streaming` |
| Metrics and context events | `LLM.configure_metrics` and `LLMModelProfile` |
| State APIs | `describe_state`, `compact_state`, `reset_state` |
| Cancellation | `TaskCancellationRequest`, `Agent.cancel_task`, `AgentClient.cancel_task` |
| Agent manifest | `agents.deck.agent_manifest()` |

## 4. Run A One-Shot Task

```bash
cargo run --manifest-path cli/Cargo.toml -- run "Explain the auth flow and identify the riskiest file"
```

This path uses the same Python core and ProtoLink runtime as the TUI, but prints
the final answer, trace, timeline, and proposed diff directly to stdout.

## 5. Run In The Fullscreen TUI

```bash
cargo run --manifest-path cli/Cargo.toml -- start
```

Then type a normal request:

```text
explain @src/auth.rs and propose a safer JWT flow
```

Typing `@` opens the project file picker. The selected file or directory is
inserted as an `@path` tag and loaded as bounded read-only context before the
agent run starts.

## 6. Inspect Context Before Running

Use Context Loom as a preview tool:

```text
/context how does the runtime send tasks to protolink
```

This builds a source-cited Context Pack without calling the model. Plain user
tasks still receive automatic Context Loom injection in the background.

## 7. Debug A Failed Run

For a copy-pasteable artifact:

```bash
PROTOAGENT_TRACE=1 cargo run --manifest-path cli/Cargo.toml -- run "your failing task" 2>&1 | tee /tmp/protoagent-debug.txt
tail -n 80 "${PROTOAGENT_CONFIG_DIR:-$HOME/.protoagent}/traces.jsonl"
```

`proto-cli run` prints `AGENT TRACE` and `AGENT TIMELINE` sections. The TUI
`/trace` command renders the latest trace in the UI, while `traces.jsonl`
contains durable ProtoLink telemetry only when `PROTOAGENT_TRACE=1` is enabled.
