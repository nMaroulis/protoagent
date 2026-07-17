---
title: Agent Deck
description: Runtime kernel, Architect, narrow workers, optional Scout, Guide, policies, tools, and memory boundaries.
---

The active coding mesh exposes three LLM-capable roles:

1. Architect
2. Explorer
3. Coder

Architect is the stateful controller. Explorer and Coder are stateless,
task-local workers. A fourth role, Scout, is a tool-only web-research agent that
is disabled by default. Guide is separate and only answers usage help
questions.

## Runtime Shape

The user-facing architecture is:

```text
Context Loom -> RunContract -> Architect -> Explorer/Coder/(optional Scout) -> Policy Gate -> Completion Guard
```

The ProtoLink runtime kernel owns `RunContext`, budgets, events, approval
requests, cancellation, authentication, and run reports. `run_contracts.py` classifies the
original user request before the model runs and validates the result after the
task stream finishes. Write tasks are returned as `incomplete` unless the trace
contains Coder delegation, a write approval/diff artifact, or an explicit
blocker.

## Deck Assembly

`agents/deck.py` creates the default deck and conditionally inserts Scout:

```python
{
    "explorer": create_explorer_agent(...),
    "coder": create_coder_agent(...),
    **({"scout": create_scout_agent(...)} if scout_enabled else {}),
    "architect": create_architect_agent(...),
}
```

Every LLM-capable role receives a separate ProtoLink LLM instance configured
with the selected provider and model. Architect receives durable conversation
storage. Explorer and Coder receive task-local in-memory state so their worker
calls do not accumulate long-term history. Scout has `llm=None`, no durable
storage, no enabled conversation state, and `expose_chat=False`; Architect
discovers the registered agent and calls its tools rather than asking Scout to
generate prose.

The embedded deck also receives a per-run ProtoLink `APIKeyAuth` bundle. The
runtime generates the credential automatically, passes the authenticator and
credential to every enabled agent, and uses the same credential for the
CLI-side `AgentClient`. Users do not need to configure this mesh token.

## Prompt Profiles

`prompt_profiles.py` defines the model-capability overlays used by Architect,
Explorer, and Coder. The base role prompts keep invariant behavior such as
delegation, read-only exploration, and approval-gated writes. A prompt profile
then tunes reasoning depth, delegation cadence, evidence discipline, and final
answer style.

Configured modes:

| Mode | Intended use |
| --- | --- |
| `auto` | Infer the profile from active provider/model. This is the default. |
| `small` | 7B/8B and heavily quantized local models; short, explicit, one-step-at-a-time instructions. |
| `medium` | Capable local or mid-tier models; balanced planning and evidence gathering. |
| `large` | Strong local/cloud models; more autonomous decomposition and verification. |
| `api` | Frontier hosted/API models; highest-autonomy coordination with rigorous evidence and validation expectations. |

Shell:

```bash
proto-cli agents profile
proto-cli agents profile api
proto-cli agents small
```

TUI:

```text
/agents profile
/agents profile large
/agents api
```

The resolved profile is included in `doctor()`, `/check`, `/agents`, runtime
progress, and `RunContext.metadata["prompt_profile"]`. The inferred contract is
included in `RunContext.metadata["run_contract"]`. ProtoLink still owns agent
calls, tool calls, policy approvals, events, memory, and reports; prompt
profiles only change the instructions given to each LLM-capable role.

## Shared Agent Helpers

`agents/common.py` provides:

| Helper | Purpose |
| --- | --- |
| `create_selected_llm()` | Create a ProtoLink LLM from the active provider/model. |
| `conversation_storage(agent_name)` | SQLite storage in `~/.protoagent/conversations.sqlite`. Currently used for the stateful Architect namespace. |
| `resolve_agent_url()` | Explicit URL, environment URL, or default local URL. |
| `create_configured_transport()` | Build a concrete ProtoLink transport with shared limits, health, lifecycle, and metrics contracts. |
| `with_prompt_profile()` | Attach the resolved model-capability prompt overlay. |
| `with_workspace_contract()` | Attach active project path and file-write rules to each system prompt. |

## Architect

Source: `core/protoagent_core/agents/architect.py`

Architect receives the user-facing task from the CLI. It owns intent
classification, routing, delegation, durable conversation memory, and final
answers.

Capabilities:

| Capability | Effect |
| --- | --- |
| `agent.delegate` | allow |
| `llm.history.compact` | allow |
| `state.compact` | allow |
| `state.describe` | allow |
| `state.reset` | allow |
| default | deny |

Architect has no direct workspace read or write tools. It should delegate to
Explorer for evidence and Coder for file changes. Because workers are
stateless, Architect handoffs must include the objective, relevant paths,
evidence, and acceptance criteria for the current task.

## Explorer

Source: `core/protoagent_core/agents/explorer.py`

Explorer is a stateless read-only worker. It builds Context Packs, reads files,
searches, checks git status, and can request a focused Context Loom pack. It
does not persist conversation history between tasks.

Tools:

| Tool | Capability | Purpose |
| --- | --- | --- |
| `read_file(path)` | `workspace.read` | Read UTF-8 text with line numbers. |
| `list_directory(path=".")` | `workspace.read` | List workspace files and folders. |
| `search_regex(pattern, path=".", file_filter=".*")` | `workspace.read` | Regex search over text files. |
| `get_git_status()` | `workspace.read` | Return `git status --short`. |
| `build_context_pack(query)` | `workspace.read` | Build source-cited Context Loom evidence. |

Policy:

| Capability | Effect |
| --- | --- |
| `workspace.read` | allow |
| default | deny |

## Coder

Source: `core/protoagent_core/agents/coder.py`

Coder is the stateless worker that can prepare file modifications. It does not
get Explorer's broad read/search tools. It is expected to receive enough context
from Architect and Explorer for the current task.

Tools:

| Tool | Capability | Purpose |
| --- | --- | --- |
| `generate_unified_diff(path, updated_content, original_content=None)` | `workspace.write` | Replace a file after preview and approval. |
| `create_new_file(path, content)` | `workspace.write` | Create a file after preview and approval. |

Policy:

| Capability | Effect |
| --- | --- |
| `workspace.write` | require approval |
| default | deny |

The action builder creates a `RunAction` with a `text/x-diff` preview artifact.
The write helper only executes after ProtoLink receives an approving
`ApprovalDecision`. If a write task finishes without Coder, approval/diff
artifacts, or an explicit blocker, runtime completion validation returns the run
as `incomplete`.

## Scout

Source: `core/protoagent_core/agents/scout.py`

Scout is an optional, stateless, tool-only network worker. It isolates external
research from repository exploration and mutation. The default config is:

```json
{
  "optional_agents": {
    "scout": {
      "enabled": false
    }
  }
}
```

Toggle it from the shell or TUI:

```bash
proto-cli agents scout on
proto-cli agents scout off
```

```text
/agents scout on
/agents scout off
```

Changes apply to the next run. Disabled means the factory is not called, the
agent is not started, and Architect cannot discover it.

Scout exposes fresh instances of the ProtoLink 0.6.6 built-ins:

| Tool | Capability | Behavior |
| --- | --- | --- |
| `web_search(query, engine=..., freshness=...)` | `network.read` | Bounded normalized results from Brave, DuckDuckGo, or English Wikipedia. |
| `fetch_url(url)` | `network.read` | Fetch bounded text from public HTTP(S) URLs on standard ports. |

Brave is the default engine and reads `BRAVE_SEARCH_API_KEY` only when invoked.
DuckDuckGo is keyless best-effort search. English Wikipedia is keyless and
supports only `freshness="any"`. Registering Scout does not itself make a
network request.

`fetch_url` rejects private/loopback targets, unsafe redirects, HTTPS
downgrades, binary bodies, and oversized responses. Tool results are marked
`untrusted_content`; they are evidence, never instructions or authorization.
Scout has no workspace capability.

Policy:

| Capability | Effect |
| --- | --- |
| `network.read` | allow |
| default | deny |

## Guide

Source: `core/protoagent_core/help_agent.py`

Guide is not part of the coding mesh. It is used by `/help QUESTION` and has:

| Setting | Value |
| --- | --- |
| Registry | none |
| Tools | none |
| Delegation | false |
| Storage | none |
| State | empty |
| Policy | deny by default |

Guide receives a static manual and a redacted current-settings snapshot. It
answers ProtoAgent usage questions, not project coding questions.

## Agent Manifest

The CLI doctor and fallback paths use `agent_manifest()`:

| Agent | Role | State | Memory | Tools |
| --- | --- | --- | --- | --- |
| Architect | stateful controller | stateful | `protoagent-architect` | none |
| Explorer | stateless context worker | stateless | task-local | Context/read/search/git tools |
| Coder | stateless write worker | stateless | task-local | diff/create tools |
| Scout | optional tool-only web worker | stateless | none | `web_search`, `fetch_url` |

The manifest also reports the runtime kernel, stateful pieces, stateless
workers, `enabled`/`optional` state, and RunContract rule used by
`proto-cli agents` and `/agents`. Update this manifest when the visible topology
changes.
