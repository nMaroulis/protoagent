---
title: Troubleshooting
description: Practical debugging workflows for common ProtoAgent failures.
---

## No Project Selected

Symptom:

```text
No project selected. Use /project to choose a folder before running a task.
```

Fix:

```bash
proto-cli project set ~/projects/my-app
```

or in the TUI:

```text
/project
```

## Model Not Selected

Symptom:

```text
No model selected for provider 'ollama'
```

Fix:

```bash
proto-cli model
```

Then run:

```bash
proto-cli check
```

## ProtoLink Missing Or Not Ready

Symptom:

```text
Protolink missing
```

or runtime readiness fields are unavailable.

Fix:

```bash
source .venv/bin/activate
pip install "protolink[http,llms]>=0.6.6"
proto-cli check
```

`check` should report streaming, metrics, compaction, context, state, reports,
cancellation, logging, auth, transport, and web tools as ready. Web-tool
readiness is separate from baseline `agent_ready` because Scout is optional.

## Scout Is Off Or Search Fails

Inspect the current agent settings:

```bash
proto-cli agents scout
proto-cli check
```

Enable Scout for the next run:

```bash
proto-cli agents scout on
```

If `web_tools_ready` is unavailable, install ProtoLink 0.6.6 or newer. Brave is
the default search engine and needs:

```bash
export BRAVE_SEARCH_API_KEY=...
```

DuckDuckGo is a keyless best-effort engine; English Wikipedia is keyless
factual search. Architect can pass either to `web_search`. Wikipedia only supports
`freshness="any"`. URL fetching intentionally rejects private/loopback hosts,
nonstandard ports, unsafe redirects, binary content, and oversized responses.
These rejections are safety behavior, not general network failures.

Scout's direct delegated tools are policy- and cancellation-aware, but
ProtoLink 0.6.6 does not yet count that direct path against
`RunBudget.max_tool_calls`. Keep Scout disabled when external calls are not
needed; ProtoAgent intentionally does not add a second local budget counter.

## Localhost Runtime Fails In A Sandbox

Symptom can look like:

```text
PermissionError: [Errno 1] Operation not permitted
```

This can be an environment restriction on binding or connecting to localhost,
not necessarily a ProtoAgent regression.

Try:

```bash
PROTOAGENT_AGENT_TRANSPORT=http PROTOAGENT_STREAM=0 proto-cli run "diagnose runtime"
```

If the environment blocks even local ports, use scaffold mode to verify the
Rust/Python boundary:

```bash
PROTOAGENT_SCAFFOLD=1 proto-cli run "diagnose runtime"
```

## Context Loom Misses The Right File

Steps:

```text
/index refresh
/context the exact feature or path name
```

Then inspect:

1. Whether the file is ignored, binary, too large, or outside the active project.
2. Whether query terms match path, symbols, headings, imports, or content.
3. Whether a git-changed file should get a relevance boost.
4. Whether `Unchanged` is high: that is expected during an incremental refresh
   and means those files were not reread because size and modification time
   still matched.

Source files:

1. `core/protoagent_core/context/indexer.py`
2. `core/protoagent_core/context/packer.py`

## TUI Trace Is Hard To Share

Use one-shot capture:

```bash
PROTOAGENT_TRACE=1 proto-cli run "your failing task" 2>&1 | tee /tmp/protoagent-debug.txt
tail -n 80 "${PROTOAGENT_CONFIG_DIR:-$HOME/.protoagent}/traces.jsonl"
```

The TUI `/trace` view is for live inspection. The one-shot runner is the better
copy-paste artifact.

## API Key Looks Stale

Model inventory caches recent validation results briefly. To force a real
refresh, wait for the retry window or restart the process, then run:

```bash
proto-cli models
```

If a live run succeeds, `remember_valid_provider()` marks that provider/model
combination as recently valid.

## Approval Did Not Execute

Check:

1. Did the diff modal show a `workspace.write` approval?
2. Was the request denied?
3. Did cancellation arrive while waiting for approval?
4. Did the target path fail `safe_path()`?

Relevant files:

1. `core/protoagent_core/agents/coder.py`
2. `core/protoagent_core/runtime_bridge.py`
3. `cli/src/progress.rs`
4. `cli/src/terminal_ui/approval.rs`

## Memory Feels Wrong

First check whether persistent memory is on:

```text
/context memory
```

Then inspect model-facing memory:

```text
/context history
```

Remember:

1. `sessions.json` is a Rust UI ledger.
2. `conversations.sqlite` is model-facing ProtoLink state.
3. `/context off` makes each task use task-local state.
4. `/context reset` clears saved ProtoLink histories for the active project
   session.
