---
title: Environment Variables
description: Runtime, model, tracing, context, and config environment variables.
---

## Config And Workspace

| Variable | Purpose |
| --- | --- |
| `PROTOAGENT_CONFIG_DIR` | Overrides the default `~/.protoagent` config/state directory. |
| `PROTOAGENT_HOME` | Legacy config directory fallback used by `config.py`. |
| `PROTOAGENT_WORKSPACE` | Set by `agent_engine.process_prompt()` for downstream helpers. |

## Runtime Transport

| Variable | Default | Purpose |
| --- | --- | --- |
| `PROTOAGENT_RUNTIME_HOST` | `127.0.0.1` | Host used when generating local runtime URLs. |
| `PROTOAGENT_REGISTRY_URL` | generated | Registry URL override. |
| `REGISTRY_URL` | generated | Legacy registry URL override. |
| `PROTOAGENT_CLIENT_URL` | generated | Client URL override. |
| `CLIENT_URL` | generated | Legacy client URL override. |
| `PROTOAGENT_ARCHITECT_URL` | generated | Architect URL override. |
| `ARCHITECT_AGENT_URL` | generated | Legacy Architect URL override. |
| `PROTOAGENT_EXPLORER_URL` | generated | Explorer URL override. |
| `EXPLORER_AGENT_URL` | generated | Legacy Explorer URL override. |
| `PROTOAGENT_CODER_URL` | generated | Coder URL override. |
| `CODER_AGENT_URL` | generated | Legacy Coder URL override. |
| `PROTOAGENT_SCOUT_URL` | generated | Optional Scout URL override. |
| `SCOUT_AGENT_URL` | generated | Legacy Scout URL override. |
| `PROTOAGENT_AGENT_TRANSPORT` | `sse` | Agent transport. Use `http` for request/response; `grpc` requires the optional ProtoLink gRPC extra. |
| `PROTOAGENT_STREAM` | `1` | Set to `0` to disable streaming consumption. |
| `PROTOAGENT_AGENT_TIMEOUT` | `600` | AgentClient timeout seconds. |

## Tracing And Progress

| Variable | Default | Purpose |
| --- | --- | --- |
| `PROTOAGENT_TRACE` | `0` | Enable durable ProtoLink `LocalTraceTelemetry` JSONL traces. |
| `PROTOAGENT_STREAM_TRACE_LIMIT` | `120` | Max stream summaries retained for UI progress before suppression. |
| `PROTOAGENT_SCAFFOLD` | `0` | Set to `1` to return diagnostics without a model call. |

## Context And Memory

| Variable | Default | Purpose |
| --- | --- | --- |
| `PROTOAGENT_CONTEXT_CHARS` | `6000` local, `48000` remote | Prompt context budget before the current request. |
| `PROTOAGENT_OLLAMA_NUM_CTX` | unset | Ollama context window override below app config. |
| `OLLAMA_CONTEXT_LENGTH` | unset | Ollama runtime context window fallback. |
| `PROTOAGENT_HISTORY_BUDGET_RATIO` | `0.7` | Fraction of context window used for run-boundary history compaction. |

## Run Budget

| Variable | RunBudget field |
| --- | --- |
| `PROTOAGENT_RUN_MAX_STEPS` | `max_steps` |
| `PROTOAGENT_RUN_MAX_LLM_CALLS` | `max_llm_calls` |
| `PROTOAGENT_RUN_MAX_TOOL_CALLS` | `max_tool_calls` |
| `PROTOAGENT_RUN_MAX_SECONDS` | `max_runtime_seconds` |
| `PROTOAGENT_RUN_MAX_INPUT_TOKENS` | `max_input_tokens` |
| `PROTOAGENT_RUN_MAX_OUTPUT_TOKENS` | `max_output_tokens` |

## Provider Variables

| Variable | Provider |
| --- | --- |
| `OLLAMA_URL` | Ollama base URL default. |
| `OLLAMA_HOST` | Ollama base URL fallback. |
| `LMSTUDIO_URL` | LM Studio base URL. |
| `LLAMACPP_SERVER_URL` | llama.cpp server base URL. |
| `OPENAI_COMPATIBLE_BASE_URL` | Generic OpenAI-compatible base URL. |
| `OPENAI_API_KEY` | OpenAI key. |
| `ANTHROPIC_API_KEY` | Anthropic key. |
| `GEMINI_API_KEY` | Gemini key. |
| `DEEPSEEK_API_KEY` | DeepSeek key. |
| `OPENAI_COMPATIBLE_API_KEY` | Generic OpenAI-compatible key. |
| `BRAVE_SEARCH_API_KEY` | Brave Search key read by ProtoLink's `web_search` only when optional Scout invokes the default Brave engine. |

DuckDuckGo is keyless best-effort search, while English Wikipedia is keyless
factual search. Enabling Scout registers network tools but does not itself issue
a request.

## Common Debug Recipes

One-shot trace capture:

```bash
PROTOAGENT_TRACE=1 proto-cli run "your task" 2>&1 | tee /tmp/protoagent-debug.txt
```

No-model contract test:

```bash
PROTOAGENT_SCAFFOLD=1 proto-cli run "show runtime diagnostics"
```

Force request/response mode:

```bash
PROTOAGENT_AGENT_TRANSPORT=http PROTOAGENT_STREAM=0 proto-cli run "task"
```

Use disposable state:

```bash
PROTOAGENT_CONFIG_DIR=/tmp/protoagent-smoke proto-cli check
```
