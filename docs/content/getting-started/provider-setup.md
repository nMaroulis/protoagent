---
title: Provider Setup
description: Configure local and API providers for ProtoAgent.
---

ProtoAgent stores provider configuration in `~/.protoagent/config.json` by
default. The Python core owns the config schema and returns a redacted view to
the Rust UI.

## Supported Providers

| Provider id | Kind | Default base URL or behavior |
| --- | --- | --- |
| `ollama` | local server | `http://localhost:11434` |
| `lmstudio` | OpenAI-compatible local server | `http://localhost:1234/v1` |
| `llama.cpp-server` | local server | `http://localhost:8080` |
| `llama.cpp-local` | local files | Scans common model folders for `.gguf` files. |
| `openai-compatible` | OpenAI-compatible server | `http://localhost:1234/v1` unless overridden. |
| `openai` | API | Requires `OPENAI_API_KEY` or stored config key. |
| `anthropic` | API | Requires `ANTHROPIC_API_KEY` or stored config key. |
| `gemini` | API | Requires `GEMINI_API_KEY` or stored config key. |
| `deepseek` | API | Requires `DEEPSEEK_API_KEY` or stored config key. |

## Select A Model

```bash
cargo run --manifest-path cli/Cargo.toml -- model
```

The picker:

1. Calls `protoagent_core.agent_engine.list_models(validate_api_keys=True)`.
2. Displays local server status, configured API providers, and model choices.
3. Prompts for missing API keys when needed.
4. Saves the active provider/model through `set_active_model()`.

Inside the TUI, use:

```text
/model
```

## Store An API Key

```bash
cargo run --manifest-path cli/Cargo.toml -- key openai
```

Inside the TUI:

```text
/key openai
```

Keys are stored in the config file unless an environment variable is present.
The displayed config is always redacted by `visible_config()`.

## Context Window For Ollama

Ollama is currently the provider with app-managed context-window control.

```text
/context window 16k
/context window auto
```

The selected value is sent as Ollama `num_ctx` and also recorded in ProtoLink's
`LLMModelProfile`, so the Rust context meter and ProtoLink metrics use the same
window.

## Validation Cache

API key validation is intentionally cached for a short time. A successful key
validation is reused for about ten minutes, while uncertain or failed validation
can be retried quickly. Live model runs call `remember_valid_provider()` so the
UI can reflect a recently successful provider/model pair.
