---
title: Safety And Tools
description: Native execution tools, explicit capabilities, prepared approvals and workspace boundaries.
---

ProtoLink owns process and recoverable filesystem execution. ProtoAgent supplies
its project roots, capability policy, authenticated approval UI, storage paths
and coding acceptance criteria. Tool registration performs no command execution.

## Explorer and Scout

Explorer keeps the application-specific `read_file`, `list_directory`,
`search_regex`, `get_git_status` and `build_context_pack` tools. `safe_path`
resolves the selected project and rejects outside paths. Search, indexing and
file picking skip symlinks and common binary/ignored directories. Explicit reads
still pass through the path boundary.

| Explorer tool | Limit or behavior |
| --- | --- |
| `read_file` | UTF-8, at most 240000 bytes, optional line numbers |
| `list_directory` | Ignores build/cache directories; returns size/type metadata |
| `search_regex` | At most 120 matches; skips binary/large files |
| `get_git_status` | Short, bounded `git status --short` probe |
| `build_context_pack` | Source-cited Context Loom evidence |

Optional Scout registers ProtoLink `web_search` and `fetch_url` under
`network.read`. It is off by default, has no model or filesystem tools, and
returns untrusted evidence. Separately approved host commands can use the
network even when Scout is off.

## Coder: native recoverable files

```python
for tool in filesystem_tools(roots=[project], checkpoints=store):
    coder.add_tool(tool)
```

| Tool | Capability | Behavior |
| --- | --- | --- |
| `create_file(path, content)` | `filesystem.write` | Create an absent file after diff approval |
| `replace_file(path, content)` | `filesystem.write` | Replace an existing file after diff approval |
| `preview_change(change_id)` | `filesystem.read` | Inspect native state, conflict, uncertainty and reverse diff |
| `restore_change(change_id)` | `filesystem.restore` | Restore a current applied change after a separate approval |

Writes and restoration explicitly require approval; reads are allowed. The
native POSIX implementation uses descriptor-relative operations, rejects symlink
components and stale preimages, and saves original bytes/modes before atomic
mutation. Paths are absolute, under configured roots, with existing parents.
New files default to 0600. The application's private storage is excluded as a
Coder target. The project namespace has one live writer.

The old app diff/write helpers and checkpoint mutation code are removed. Native
`prepared`, `restoring` and `uncertain` records require inspection; the application
blocks further effects in that run. Failed restoration never forces an overwrite.
Legacy checkpoints stay readable without being imported as native execution.

## Verifier: native processes

```python
verifier.add_tool(process_tool(max_timeout_seconds=600, max_output_bytes=32768))
```

Architect submits `execute_command` with explicit argv, absolute cwd, env,
timeout_seconds and max_output_bytes. `WorkspacePolicy` keeps cwd in the project,
then native `process.execute` policy requires approval. The local backend runs
with host permissions and provides **no sandbox isolation**. Environment is
explicit; `{}` does not inherit credentials or PATH. Standard input is closed.

ProtoLink owns output collection, exit status, truncation, timing, native budgets,
process-group cleanup and cancellation. `process.output` and `process.finished`
are native events; successful dispatch emits an `action.completed` receipt.
Approval is authorization, never proof that execution succeeded.

## Rust approval rendering

The approval request retains the original native fingerprint. The display copy
masks recovery bytes and known credentials without changing the authorized action.
Rust echoes both the exact request ID and fingerprint; the application supplies
the trusted `ApprovalScope`. Native broker outcomes distinguish stale, duplicate,
expired and resolved decisions.

Native filesystem previews use `Artifact(kind="preview")` with a text diff and
`metadata.preimage`. Native process previews contain a JSON part with the frozen
specification and execution boundary. Neither requires a `media_type`; the UI
also understands older typed preview records for history compatibility.

## Capabilities and acceptance

| Agent | Explicit grants; other protected capabilities are denied |
| --- | --- |
| Architect | Delegation and state/history operations |
| Explorer | `workspace.read` |
| Coder | `filesystem.read`; approval for write and restore |
| Verifier | Approval for `process.execute` |
| Scout | `network.read`, when enabled |
| Guide | No tools, state or delegation |

The application uses native `CompletionCheck` and `CompletionValidator` over
execution receipts and resource revisions. A code change is not certified by a
preview or an approving UI response. Graph bounds repairs independently of
transport retry policy. See [Verify & Recover](../cli/verification-and-recovery.md)
for observable statuses and platform/recovery limits.
