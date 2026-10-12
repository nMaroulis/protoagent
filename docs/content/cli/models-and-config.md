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
| `optional_agents.scout.enabled` | Whether tool-only Scout is registered for future runs. Defaults to `false`. |
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

## Ollama Tool Calling

Tool calling defaults to `auto`. ProtoAgent passes that policy to ProtoLink;
the engine reads Ollama's `/api/show` metadata when resolving the protocol.
If the model advertises `tools`, ProtoLink sends
native tool declarations and consumes structured calls. Models without that
capability, or servers without usable metadata, keep the JSON action protocol.
The authenticated metadata request uses the exact model tag and endpoint.
ProtoLink caches evidence for five minutes, invalidates it when configuration
changes, and exposes `refresh_capabilities()` to explicit API callers. Discovery
does not generate text or execute tools. Each inference keeps its selected
protocol consistent through prompting and correction.

To override the choice, set `providers.ollama.tool_calling` in `config.json` to
`auto`, `native` or `json`. Without that config field, the environment can set it:

```bash
PROTOAGENT_OLLAMA_TOOL_CALLING=json proto-cli run "Explain this project"
```

Native support is an advertised capability, not a guarantee of model accuracy.
Recognizable malformed action envelopes enter ProtoLink's parse correction
loop in JSON/native modes. Completed observations remain available; there is
no run restart or application parser executing displayed answers. Explanatory
prose and examples inside explicit `final.content` remain data. Use `/trace`
to inspect correction and actual execution receipts.

## Request correction limits

`providers.PROVIDER.max_tool_corrections` in `config.json` sets the native
allowance, default two corrective turns per inference. For example:

```json
{"providers":{"ollama":{"tool_calling":"auto","max_tool_corrections":2}}}
```

Unknown tools, invalid argument schemas, no-change application rejections and
invalid worker requests share the allowance. Successful actions do not replenish
it. Subsequent model calls and actual tools also consume existing run budgets;
the limit is a runtime setting, never a generation parameter. Parse failures use
a separate native circuit breaker. `/trace` and `/timeline` show rejected requests
and correction exhaustion separately from executed tools and permission denials.
Denials, execution failures and uncertain effects do not receive blind retries.

Other providers accept an explicit `tool_calling` policy through the same config
field and otherwise retain adapter defaults. With `fallback_models`, RoutedLLM
uses its portable JSON protocol and retains the configured correction allowance.

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

## Optional Scout

Scout is configured independently from the prompt profile because it changes
the network trust boundary, not model reasoning depth:

```text
/agents scout
/agents scout on
/agents scout off
```

```bash
proto-cli agents scout
proto-cli agents scout on
proto-cli agents scout off
```

Scout is off by default and changes apply to the next run. When enabled it is a
tool-only ProtoLink agent with `web_search` and `fetch_url`; it does not select
or call another LLM. Brave search reads `BRAVE_SEARCH_API_KEY` when invoked.
The key is not stored in ProtoAgent's provider configuration.

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
| `/agents scout` | Current optional Scout state and tool inventory. |
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
