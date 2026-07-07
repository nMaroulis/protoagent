---
title: Agent Deck
description: Runtime kernel, RunContract, Architect, stateless workers, Guide, policies, tools, and memory boundaries.
---

The active coding mesh exposes three LLM-capable roles:

1. Architect
2. Explorer
3. Coder

They no longer have the same state model. Architect is the stateful controller.
Explorer and Coder are stateless, task-local workers. Guide is separate and
only answers usage help questions.

## Runtime Shape

The user-facing architecture is:

```text
Context Loom -> RunContract -> Architect -> Explorer/Coder -> Policy Gate -> Completion Guard
```

The ProtoLink runtime kernel owns `RunContext`, budgets, events, approval
requests, cancellation, authentication, and run reports. `run_contracts.py` classifies the
original user request before the model runs and validates the result after the
task stream finishes. Write tasks are returned as `incomplete` unless the trace
contains Coder delegation, a write approval/diff artifact, or an explicit
blocker.

## Deck Assembly

`agents/deck.py` creates the deck:

```python
{
    "explorer": create_explorer_agent(...),
    "coder": create_coder_agent(...),
    "architect": create_architect_agent(...),
}
```

Every LLM-capable role receives a separate ProtoLink LLM instance configured
with the selected provider and model. Architect receives durable conversation
storage. Explorer and Coder receive task-local in-memory state so their worker
calls do not accumulate long-term history.

The embedded deck also receives a per-run ProtoLink `APIKeyAuth` bundle. The
runtime generates the credential automatically, passes the authenticator and
credential to Architect, Explorer, Coder, and uses the same credential for the
CLI-side `AgentClient`. Users do not need to configure a token for local runs.

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
| `set_transport_timeout()` | Apply long timeouts across ProtoLink transports. |
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

Explorer is a stateless read-only worker. It builds context maps, reads files,
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

The manifest also reports the runtime kernel, stateful pieces, stateless
workers, and RunContract rule used by `proto-cli agents` and `/agents`. Update
this manifest when the visible topology changes.
