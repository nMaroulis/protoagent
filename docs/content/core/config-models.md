---
title: Config And Models
description: Provider configuration, discovery, LLM construction, metrics profiles, and validation.
---

Provider configuration and model discovery live in `config.py`, `models.py`, and
`llm.py`.

## Config Defaults

`default_config()` creates a provider entry for every known provider. Existing
config is deep-merged over defaults, so new provider definitions appear without
manual user migration.

Important constants:

| Constant | Meaning |
| --- | --- |
| `CONFIG_VERSION` | Current config schema version. |
| `CONFIG_DIR` | `PROTOAGENT_CONFIG_DIR`, `PROTOAGENT_HOME`, or `~/.protoagent`. |
| `CONFIG_PATH` | `config.json` under `CONFIG_DIR`. |
| `MIN_CONTEXT_WINDOW` | 2048 tokens. |
| `MAX_CONTEXT_WINDOW` | 2097152 tokens. |
| `API_PROVIDERS` | Providers that accept API keys. |
| `LOCAL_PROVIDERS` | Local providers. |

The top-level `agent_prompt_profile` key controls the prompt overlay used by
Architect, Explorer, and Coder. Valid values are `auto`, `small`, `medium`,
`large`, and `api`. `set_agent_prompt_profile()` persists the value and
`prompt_profile_status()` reports the configured and resolved mode for display.

`auto` resolves from the active provider/model. Hosted API providers resolve to
`api`; local model names with 7B/8B hints resolve to `small`; mid-size names
such as 14B/27B resolve to `medium`; 30B+ or `large` model names resolve to
`large`.

## Provider Aliases

`normalize_provider()` accepts common aliases:

| Alias | Canonical id |
| --- | --- |
| `lm-studio` | `lmstudio` |
| `llamacpp`, `llama-cpp`, `llama.cpp` | `llama.cpp-server` |
| `llama-cpp-local`, `llama.cpp-local` | `llama.cpp-local` |
| `openai-compatible-server`, `openai-compatible-local`, `openai-compatible-api` | `openai-compatible` |

## Visible Config

`visible_config()` returns display-safe config:

1. Resolves whether a key is set from config or environment.
2. Redacts the key.
3. Marks `from_env`.
4. Adds `config_path`.

The CLI should display this view, not raw config.

## Model Discovery

`discover_models(validate_api_keys=False)` returns:

```json
{
  "config_path": "...",
  "api_key_validation": false,
  "active_provider": "ollama",
  "active_model": "qwen2.5-coder:7b",
  "providers": []
}
```

Each provider record includes id, name, kind, status, configured flag, base URL,
hint, key metadata, and normalized model records.

## API Key Validation

Validation strategy:

1. Try ProtoLink validation through the configured LLM when possible.
2. Fall back to provider model-list endpoint.
3. Cache valid results longer than uncertain results.
4. Mark a provider as recently valid after a successful live run.

Cache TTLs:

| Result | TTL |
| --- | --- |
| Valid | 600 seconds |
| Invalid or unverified | 30 seconds |

The cache key hashes the API key so raw secrets are not stored in memory keys.

## LLM Construction

`create_llm_from_config(provider=None, model=None)`:

1. Resolves provider config with API key.
2. Maps ProtoAgent provider ids to ProtoLink provider ids.
3. Calls `protolink.llms.factory.create_llm()`.
4. Configures metrics with `LLMModelProfile`.

For Ollama, `llm_kwargs()` injects:

```python
model_params["num_ctx"] = ollama_context_window(cfg)
```

The same value is written into `LLMModelProfile.context_window`, so request
parameters and UI metrics do not drift apart.

## ProtoLink Readiness

`validate_protolink()` checks that the installed ProtoLink exposes the runtime
features ProtoAgent expects:

| Readiness flag | Meaning |
| --- | --- |
| `streaming_ready` | Agent and client support streaming task channels. |
| `metrics_ready` | `LLM.configure_metrics()` and `LLMModelProfile` exist. |
| `compaction_ready` | LLM history compaction support exists. |
| `context_manifest_ready` | Context manifest support exists. |
| `run_report_ready` | `RunRecorder` and redaction support exist. |
| `state_ready` | describe, reset, and compact state APIs exist on Agent and AgentClient. |
| `cancellation_ready` | task cancellation APIs exist. |
| `logging_ready` | the shared no-output `QuietLogger` exists for embedded agent runs. |
| `auth_ready` | `APIKeyAuth` plus agent and HTTP transport credential hooks exist. |

`agent_ready` is true only when all expected runtime capabilities are ready.
