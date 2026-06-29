---
title: Models And Config
description: Model discovery, provider setup, config files, key storage, and status rendering.
---

The CLI renders model/provider state, but the Python core owns discovery and
configuration. Rust calls `list_models()`, `get_config()`, `add_api_key()`, and
`set_model()` through PyO3.

## Config Path

Default:

```text
~/.protoagent/config.json
```

Override:

```bash
export PROTOAGENT_CONFIG_DIR=/tmp/protoagent-config
```

Legacy home override:

```bash
export PROTOAGENT_HOME=/tmp/protoagent-config
```

## Default Provider Schema

The config contains:

| Key | Meaning |
| --- | --- |
| `version` | Config version. |
| `active_provider` | Canonical provider id. Defaults to `ollama`. |
| `agent_prompt_profile` | Prompt profile for the agent deck: `auto`, `small`, `medium`, `large`, or `api`. |
| `providers` | Per-provider labels, base URLs, model ids, API keys, optional context windows. |

`load_config()` deep-merges saved config over defaults so new providers appear
without manual migration.

## Provider Discovery

`discover_models(validate_api_keys)` returns a normalized inventory.

| Provider | Discovery path |
| --- | --- |
| Ollama | `GET /api/tags`, falling back to `ollama list`. |
| LM Studio | OpenAI-compatible `/v1/models`, falling back to local model folder scan. |
| OpenAI-compatible | Configured base URL `/v1/models`, with optional bearer key. |
| llama.cpp server | `/v1/models`, falling back to `/props`. |
| llama.cpp local | Scans common model directories for `.gguf` files. |
| Cloud APIs | Static curated model choices plus optional lightweight key validation. |

The CLI renders provider cards and compact provider strips from this inventory.

## Agent Prompt Profile

The deck prompt profile controls how Architect, Explorer, and Coder reason and
coordinate for the selected model tier. It does not replace ProtoLink's agent
calls, tool calls, memory, policies, or approvals.

```text
/agents profile
/agents profile api
```

Shell:

```bash
proto-cli agents profile
proto-cli agents profile large
```

`auto` is the default. API providers resolve to `api`; local model names with
7B/8B hints resolve to `small`; 14B/20B/27B hints resolve to `medium`; 30B+
and `large` hints resolve to `large`.

## Key Sources

Cloud API keys can come from either config or environment:

| Provider | Environment variable |
| --- | --- |
| OpenAI | `OPENAI_API_KEY` |
| Anthropic | `ANTHROPIC_API_KEY` |
| Gemini | `GEMINI_API_KEY` |
| DeepSeek | `DEEPSEEK_API_KEY` |
| OpenAI-compatible | `OPENAI_COMPATIBLE_API_KEY` |

If an environment variable is set and no config key exists, the visible config
reports `from_env: true`. Display output is redacted through `redact_key()`.

## Key Validation Status

API providers report a key status:

| Status | Meaning |
| --- | --- |
| `missing` | No key found. |
| `set` | Key exists but was not validated in this call. |
| `valid` | Provider accepted the key or a live model run succeeded. |
| `invalid` | Provider rejected the key. |
| `unverified` | Key exists but validation was inconclusive. |
| `not-required` | Generic compatible endpoint responded without a key. |

The TUI model panel uses these statuses to decide whether to prompt for a key
before model selection.

## Selecting A Model

Shell:

```bash
proto-cli model
```

TUI:

```text
/model
```

Flow:

1. Load inventory with API key validation.
2. Choose provider.
3. Prompt for a key if the provider needs one.
4. Choose a discovered model or enter a custom model id/path.
5. Prompt for base URL when the provider is server-compatible.
6. Persist config through `set_active_model()`.

## Display Commands

| Command | Output |
| --- | --- |
| `proto-cli models` | Provider strip plus provider cards. |
| `proto-cli config` | Redacted active provider config. |
| `proto-cli dashboard` | Project, provider, model, provider strip, agent graph. |
| `proto-cli agents profile` | Current configured and resolved prompt profile. |
| `/models` | Pinned TUI inventory panel. |
| `/config` | Pinned TUI config panel. |
| `/agents profile` | Current configured and resolved prompt profile. |
| `/check` | Runtime plus active provider status. |

## Context Window Control

Only Ollama is app-controllable today:

```text
/context window 16k
/context window auto
```

The core enforces:

| Limit | Value |
| --- | --- |
| Minimum | 2048 tokens |
| Maximum | 2097152 tokens |

The effective value priority is:

1. App config `context_window`.
2. `PROTOAGENT_OLLAMA_NUM_CTX`.
3. Provider `model_params.num_ctx`.
4. `OLLAMA_CONTEXT_LENGTH`.
5. ProtoAgent default, 8192.

## What To Update

If provider support changes, update:

1. `core/protoagent_core/config.py`
2. `core/protoagent_core/models.py`
3. `core/protoagent_core/llm.py`
4. `cli/src/terminal_ui/model_picker.rs`
5. this page
6. `Core / Config And Models`
