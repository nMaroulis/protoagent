---
title: Agent Deck
description: Required coding roles, optional Tester/Scout/MCP, Guide, policies, tools, and memory boundaries.
---

The default coding mesh exposes four LLM-capable roles:

1. Architect
2. Explorer
3. Tester (optional, enabled by default)
4. Coder

Architect is the stateful controller. Explorer, Tester and Coder are stateless,
task-local workers. Verifier is an always-registered tool-only command worker. Scout is a
tool-only web-research agent that is disabled by default. MCP is a model-free
broker, also disabled by default. Guide is separate and only answers usage help
questions.

## Runtime Shape

The user-facing architecture is:

```text
Context Loom -> TaskRecord/RunContract -> Architect -> Explorer/Coder/Verifier/(optional Tester/Scout/MCP) -> Policy Gate -> Completion Guard
```

The ProtoLink runtime kernel owns `RunContext`, budgets, events, approval
requests, cancellation, authentication, and run reports. `run_contracts.py` classifies the
original user request before the model runs; `verification.py` supplies native
completion checks after execution. Write tasks are returned as `incomplete` when the trace
lacks an executed native file change at its current resource revision.
Approval, preview and model prose never count as execution. Native Graph limits
allow one initial attempt and at most two repairs after completed failing checks.

## Deck Assembly

`agents/deck.py` creates required workers and conditionally inserts optional workers:

```python
{
    "explorer": create_explorer_agent(...),
    **({"tester": create_tester_agent(...)} if tester_enabled else {}),
    "coder": create_coder_agent(...),
    "verifier": create_verifier_agent(...),
    **({"scout": create_scout_agent(...)} if scout_enabled else {}),
    **({"mcp": create_mcp_agent(...)} if mcp_enabled else {}),
    "architect": create_architect_agent(...),
}
```

Every LLM-capable role receives a separate ProtoLink LLM instance configured
with the selected provider and model. Architect receives durable conversation
storage. Explorer, Tester and Coder receive task-local in-memory state so their worker
calls do not accumulate long-term history. Scout has `llm=None`, no durable
storage, no enabled conversation state, and `expose_chat=False`; Architect
discovers the registered agent and calls its tools rather than asking Scout to
generate prose.

The embedded deck also receives a per-run ProtoLink `APIKeyAuth` bundle. The
runtime generates the credential automatically, passes the authenticator and
credential to every enabled agent, and uses the same credential for the
CLI-side `AgentClient`. Users do not need to configure this mesh token.

The default CLI runtime constructs the deck with `local_children=True` and
`transport=None`. Architect receives the enabled workers as its native
`subagents` roster. Each call gets an independent conversation; repeated jobs for
one worker do not share its previous assignment. Child actions must satisfy the
Architect's delegation ceiling and the worker's own policy. Architect still has
no mutation or process tools. `SubagentLimits` caps depth at one, concurrency at
one and children at 32 per attempt. Shared Graph budgets also cover child work.
Explicit transport mode uses authenticated Registry discovery instead.

Every model-facing role receives native ContextPolicy preparation and a
before-model hook for task state and compact small-profile metadata. Large results
are progressively retrievable through `read_context_artifact`; it reads scoped
observations and grants no repository or external-tool authority.

## User questions

Architect also has native `ask_user(question, options=None)`. It uses this for
requirements or preferences that repository evidence cannot resolve, with one
concise question and up to three suggested answers. Workers do not prompt the
user directly; they report missing information to Architect. The engine places
the answer in tool history and continues the same task. Decline and timeout
supply no answer, and feedback never replaces an execution approval. See
[runtime interaction](runtime.md#user-questions-and-live-continuation).

## Prompt Profiles

`prompt_profiles.py` defines the model-capability overlays used by Architect,
Explorer, Tester, and Coder. The base role prompts keep invariant behavior such as
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
| `task.manage` | allow |
| `llm.history.compact` | allow |
| `state.compact` | allow |
| `state.describe` | allow |
| `state.reset` | allow |
| default | deny |

Architect has task-status, planning and bounded source-packet tools, with no write
tools. It delegates broader exploration to Explorer and file changes to Coder. Because workers are
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
get Explorer's broad search tools. It has bounded `read_file` access and can
request missing context through `report_task`. Architect can pass real source
packets using `worker_packet`. The preferred edit is an exact replacement with a
full-file revision, avoiding reproduction of unaffected source.

Tools registered directly from `protolink.tools.builtins.filesystem_tools()`:

| Tool | Capability | Purpose |
| --- | --- | --- |
| `read_file(path, start_line, end_line)` | `workspace.read` | Read a bounded source span and revision |
| `edit_file(path, old, new, expected_revision)` | `filesystem.write` | Prepare one exact native replacement |
| `create_file(path, content)` | `filesystem.write` | Approved creation of an absent file |
| `replace_file(path, content)` | `filesystem.write` | Approved replacement of an existing file |
| `preview_change(change_id)` | `filesystem.read` | Inspect native recovery state, conflict and diff |
| `restore_change(change_id)` | `filesystem.restore` | Separately approved restoration at the exact current revision |

Both write and restore capabilities require approval; read is allowed and the
default is deny. ProtoLink owns prepared diff artifacts, revision checks, exact
preimages and checkpoint persistence. Paths must be absolute under the project
root, without symlinks, with existing parents. These tools require POSIX.

The Coder factory's `tool_only=True` mode skips model construction for CLI undo.
The application supplies a dedicated `StorageCheckpointStore` and an
`ApprovalBroker` as the actual approval handler.

## Tester

Tester is task-local and read-only. It inspects source and existing tests, proposes
acceptance criteria and regression cases, selects discovered check IDs, and
classifies observed failures. It cannot edit or run commands. Coder implements
tests; Verifier supplies measured execution evidence. Task status and validated
worker reports use `task.manage`. Tester is optional and enabled by default; delegation is
chosen by Architect rather than required on every request.

## Verifier

Verifier has `llm=None`, `state=[]` and `expose_chat=False`. It registers native
`process_tool(max_timeout_seconds=600, max_output_bytes=32768)`.
Architect normally calls `run_check(check_id, phase)` using the frozen repository
plan. Baseline permits later edits; verify closes editing. General preparation
uses `execute_command` with argv, absolute cwd, explicit env,
timeout_seconds and max_output_bytes. `process.execute` requires approval.

Native results include exit_code, stdout, stderr, timed_out, canceled, truncated,
duration_seconds and budget_exceeded. ProtoLink owns execution and cleanup;
commands run on the host without sandbox isolation. No environment is inherited.

`verification.py` defines application acceptance using native `CompletionCheck`
and `CompletionValidator`. `workflow.py` bounds repair attempts with Graph and
keeps edits before final verification in each attempt. No interactive application process runner or
integer file-change revision counter remains.
See [Verify & Recover](../cli/verification-and-recovery.md).

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

Scout exposes fresh instances of the ProtoLink 0.8.0 built-ins:

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
The manual covers Tester, Scout and MCP toggles in both TUI and shell form,
their defaults, required roles and when settings take effect. Every help call
receives their current enabled/disabled states. Guide explains how to configure
the harness; it has no tools to change settings itself.

## Agent Manifest

The CLI doctor and fallback paths use `agent_manifest()`:

| Agent | Role | State | Memory | Tools |
| --- | --- | --- | --- | --- |
| Architect | stateful controller | stateful | `protoagent-architect` | task status, planning, source packets |
| Explorer | stateless context worker | stateless | task-local | Context/read/search/git tools |
| Tester | read-only regression designer | stateless | task-local | read/search, task status and reports |
| Coder | stateless write worker | stateless | task-local | bounded read, exact edit, native create/replace/restore |
| Verifier | tool-only command worker | stateless | none | `run_check`, `execute_command` |
| Scout | optional tool-only web worker | stateless | none | `web_search`, `fetch_url` |

The manifest also reports the runtime kernel, stateful pieces, stateless
workers, `enabled`/`optional` state, and RunContract rule used by
`proto-cli agents` and `/agents`. Update this manifest when the visible topology
changes.


## Optional-worker controls and MCP

Architect, Explorer, Coder and Verifier cannot be disabled. Tester defaults on;
Scout and MCP default off. Use `proto-cli agents tester|scout|mcp on|off`, or
`/agents tester|scout|mcp on|off`. Settings persist user-wide and apply to the
next run. Disabled workers are not constructed or advertised, and Architect's
instructions explain their absence. When Tester is off, Architect defines
criteria and Coder adds regression tests; repository verification stays required.

The MCP broker has `llm=None`, no conversation memory and three fixed tools:
`list_mcp_tools`, `mcp_tool_schema`, `call_mcp_tool`. Architect calls it with
`action="tool_call"`, never `infer`; workers request external evidence through
Architect. Discovery is lazy, schemas are returned one at a time and calls
require explicit tool allowlists and native approvals. See [MCP Broker](mcp.md)
for `mcp`/`/mcp` setup, transports, credentials, limits and effect boundaries.
