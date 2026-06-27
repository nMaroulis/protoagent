---
title: Agent Deck
description: Architect, Explorer, Coder, Guide, policies, tools, and memory namespaces.
---

The active coding mesh has three LLM-capable agents:

1. Architect
2. Explorer
3. Coder

Guide is separate and only answers usage help questions.

## Deck Assembly

`agents/deck.py` creates the deck:

```python
{
    "explorer": create_explorer_agent(...),
    "coder": create_coder_agent(...),
    "architect": create_architect_agent(...),
}
```

Every LLM-capable agent receives a separate ProtoLink LLM instance configured
with the selected provider and model. Separate instances keep prompts and
histories isolated.

## Shared Agent Helpers

`agents/common.py` provides:

| Helper | Purpose |
| --- | --- |
| `create_selected_llm()` | Create a ProtoLink LLM from the active provider/model. |
| `conversation_storage(agent_name)` | SQLite storage in `~/.protoagent/conversations.sqlite`, namespace `protoagent-<agent>`. |
| `resolve_agent_url()` | Explicit URL, environment URL, or default local URL. |
| `set_transport_timeout()` | Apply long timeouts across ProtoLink transports. |
| `with_workspace_contract()` | Attach active project path and file-write rules to each system prompt. |

## Architect

Source: `core/protoagent_core/agents/architect.py`

Architect receives the user-facing task from the CLI. It owns intent
classification, routing, delegation, and final answers.

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
Explorer for evidence and Coder for file changes.

## Explorer

Source: `core/protoagent_core/agents/explorer.py`

Explorer is read-only. It builds context maps, reads files, searches, checks git
status, and can request a focused Context Loom pack.

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
| state and history compaction capabilities | allow |
| default | deny |

## Coder

Source: `core/protoagent_core/agents/coder.py`

Coder is the only agent that can prepare file modifications. It does not get
Explorer's broad read/search tools. It is expected to receive enough context
from Architect and Explorer.

Tools:

| Tool | Capability | Purpose |
| --- | --- | --- |
| `generate_unified_diff(path, updated_content, original_content=None)` | `workspace.write` | Replace a file after preview and approval. |
| `create_new_file(path, content)` | `workspace.write` | Create a file after preview and approval. |

Policy:

| Capability | Effect |
| --- | --- |
| `workspace.write` | require approval |
| state and history compaction capabilities | allow |
| default | deny |

The action builder creates a `RunAction` with a `text/x-diff` preview artifact.
The write helper only executes after ProtoLink receives an approving
`ApprovalDecision`.

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

| Agent | Role | Memory namespace | Tools |
| --- | --- | --- | --- |
| Architect | orchestrator | `protoagent-architect` | none |
| Explorer | context | `protoagent-explorer` | Context/read/search/git tools |
| Coder | synthesis | `protoagent-coder` | diff/create tools |

Update this manifest when the visible topology changes.
