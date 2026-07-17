---
title: Core Architecture
description: PyO3 entrypoints, JSON response schemas, and the runtime prompt preparation path.
---

`agent_engine.py` is the public application API for the Rust frontend. It is a
thin but important adaptation layer around the rest of the Python core.

## PyO3-Facing Functions

| Function | Called by | Returns |
| --- | --- | --- |
| `list_models(validate_api_keys=False)` | `models`, `/models`, `/model`, `/key` | Model inventory JSON. |
| `get_config()` | `config`, dashboard, model panels | Redacted config JSON. |
| `component_versions(cli_version=None)` | `version`, `/version` | CLI, core, and ACP component version inventory JSON. |
| `add_api_key(provider, api_key)` | `key`, `/key` | Redacted config JSON after storing key. |
| `set_model(provider, model, base_url)` | `model`, `/model` | Redacted config JSON after selection. |
| `answer_help_question(question)` | `help QUESTION`, `/help QUESTION` | Guide answer JSON. |
| `get_context_settings()` | `/context window` | Active provider context-window settings. |
| `configure_context_window(value)` | `/context window 16k` | Updated context-window settings. |
| `get_agent_settings()` | `agents`, `/agents` | Prompt profile, architecture manifest, agent list, and Scout state. |
| `configure_agent_prompt_profile(value)` | `agents profile`, `/agents profile` | Updated prompt-profile settings. |
| `configure_optional_agent(name, enabled)` | `agents scout`, `/agents scout` | Updated optional-agent settings and manifest. |
| `compact_protolink_history(session_id, strategy, limit)` | `/context compact` | ProtoLink state compaction report JSON. |
| `reset_protolink_history(session_id)` | `/context reset` | ProtoLink state reset report JSON. |
| `describe_protolink_history(session_id)` | `/context history` | ProtoLink state summary JSON. |
| `doctor(workspace)` | `check`, `/check` | Runtime diagnostic JSON. |
| `context_status(workspace)` | `context`, `/context` | Context Loom index status JSON. |
| `refresh_context(workspace)` | `index refresh`, `/index refresh` | Refreshed Context Loom status JSON. |
| `context_pack(query, workspace)` | `context QUERY`, `/context QUERY` | Context Pack JSON. |
| `process_prompt(prompt, workspace, session_id, progress_path)` | `run`, TUI task loop | Core response JSON. |

Every function returns a JSON string through `_json()`, which normalizes values
for Rust.

`doctor()` also includes `component_versions` so the runtime check can display
the same component inventory alongside Python, ProtoLink, and provider status.

## `process_prompt()` Stages

`process_prompt()` is the main runtime entrypoint.

1. Resolve the workspace with `workspace_root()`.
2. Set `PROTOAGENT_WORKSPACE` for downstream helpers.
3. Emit progress that the CLI accepted the task.
4. Resolve `@file` tags through `_tagged_file_context()`.
5. Build Context Loom context through `_context_pack_for_prompt()`.
6. If `PROTOAGENT_SCAFFOLD=1`, return `_fallback_response()`.
7. Otherwise call `_model_response()`, which passes the original user prompt to
   runtime contract inference.
8. If the model path raises, return fallback diagnostics with status `fallback`.

## Runtime Prompt

`_runtime_prompt()` combines:

1. A short instruction explaining that Context Loom and tags are evidence.
2. Explicit tagged files selected with `@`.
3. The automatic Context Loom pack.
4. The original user request at the end.

The prompt explicitly states that conversation continuity comes from ProtoLink
per-agent history, not from local prompt stuffing.

The application-context budget is controlled by:

```bash
PROTOAGENT_CONTEXT_CHARS=6000
```

Defaults:

| Provider class | Default prompt-context character budget |
| --- | --- |
| Local providers | 6000 |
| Remote/API providers | 48000 |

## Tagged Files

`@` tags are parsed by `_extract_file_tags()`:

```text
@src/auth.rs
@"path with spaces.md"
```

Limits:

| Limit | Value |
| --- | --- |
| Max tagged files | 6 |
| Total tagged context | 12000 characters |
| Per tagged item | 6000 characters |

Directories are represented as bounded listings. Files are read with line
numbers. Paths are resolved by `safe_path()`.

## Response Schema

`CoreResponse` in Rust expects these fields:

| Field | Meaning |
| --- | --- |
| `status` | `answered`, `blocked`, `canceled`, `incomplete`, `ready`, or `fallback`. |
| `headline` | Short run headline. |
| `answer` | Final readable answer. |
| `thought_process` | Core diagnostics and runtime notes. |
| `file_target` | Likely or actual target paths. |
| `diff` | Concatenated approved/proposed diff previews. |
| `events` | Human-readable fallback or progress events. |
| `run_events` | Normalized ProtoLink `RunEvent` records. |
| `approval_requests` | Serialized approval requests. |
| `approval_decisions` | Serialized decisions. |
| `run_context` | Final serialized `RunContext`. |
| `run_report` | Redacted ProtoLink `RunReport`. |
| `transport_report` | ProtoLink transport capabilities, health/configuration, and per-run metrics. |
| `run_contract` | Inferred worker/artifact requirements for the original prompt. |
| `completion_validation` | Runtime validation result comparing the trace to the contract. |
| `provider` | Provider id. |
| `model` | Model id. |
| `responder` | Usually `architect`. |
| `workspace` | Active project path. |
| `warning` | Tag or runtime warnings. |
| `elapsed_ms` | End-to-end elapsed milliseconds. |

## Scaffold And Fallback

`PROTOAGENT_SCAFFOLD=1` is an intentional no-model path. It exercises:

1. Rust/Python import boundary.
2. Active workspace resolution.
3. Tagged context loading.
4. Context Loom pack building.
5. Provider config reading.
6. Agent manifest and tool registration diagnostics.

Fallback mode also uses this response shape when the live ProtoLink run fails.
That makes the CLI robust enough to show actionable diagnostics even before a
model/provider is fully configured.
