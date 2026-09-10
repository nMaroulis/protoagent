---
title: Safety And Tools
description: Workspace-safe tools, path boundaries, diff previews, and authorization.
---

The core tools are deterministic helpers exposed to Explorer and Coder through
ProtoLink tool registration. Optional Scout uses ProtoLink's first-party
network tools rather than a local duplicate. Verifier wraps bounded command
execution in ProtoLink tool dispatch and policy.

## Workspace Boundary

`workspace_root(workspace)` resolves the active project. `safe_path(path,
workspace)` then:

1. Expands user paths.
2. Resolves relative paths against the workspace root.
3. Resolves symlinks and absolute paths.
4. Rejects any path outside the workspace.

Every read, search, diff, create, and write helper goes through this boundary.

## Read-Only Tools

Explorer uses these:

| Tool | Limit or behavior |
| --- | --- |
| `read_file(path, with_line_numbers=True)` | UTF-8 only, max 240000 bytes, optional line numbers. |
| `list_directory(path=".")` | Skips ignored names and returns type/size metadata. |
| `search_regex(pattern, path=".", file_filter=".*")` | Regex search, max 120 matches, skips binary/large files. |
| `get_git_status()` | Runs `git status --short` with a short timeout. |
| `build_context_pack(query)` | Source-cited Context Loom evidence for a focused task. |

Ignored directories:

```text
.git .hg .svn .venv __pycache__ node_modules target dist build
```

Search, indexing, and the CLI file picker skip symlink entries as well as
common binary suffixes. Explicit paths still go through `safe_path()`.

## Network-Read Tools

Scout owns the dedicated public-web tools:

| Tool | Boundary |
| --- | --- |
| `web_search` | Bounded normalized sources; Brave, keyless best-effort DuckDuckGo, or English Wikipedia. |
| `fetch_url` | Public HTTP(S) URLs on standard ports only, with DNS and redirect checks plus text/size bounds. |

Both tools declare `network.read`, return untrusted content, and have no
workspace access. Scout is disabled by default, so the default deck does not
register these web tools. Separately approved Verifier commands may use the network. Enabling Scout registers the
factories but still makes no request until Architect invokes a tool.

## Write Tools

Coder uses these through approval-gated tools:

| Tool | Purpose |
| --- | --- |
| `generate_unified_diff(path, updated_content, original_content=None)` | Preview and apply an approved file replacement, retaining a checkpoint. |
| `create_new_file(path, content)` | Preview and create an approved file, retaining a checkpoint. |
| `restore_checkpoint(checkpoint_id="latest")` | Preview and approve undoing one Coder file change. |

The tool exposed to the model is not a raw filesystem write. The Coder factory
wraps it in an action builder that first creates a `RunAction` with a diff
artifact. ProtoLink policy pauses before the checkpoint/write helper runs. The
action builder fills an internal `expected_hash` argument from actual file bytes;
execution rechecks it after approval. The model does not choose the preimage.

## Command Execution

Verifier exposes `run_command(argv, cwd=".", timeout_seconds=120)` with
`shell.execute: require_approval`. It has no LLM or conversation state. The
prepared `RunAction` carries a `text/plain` preview of the exact command,
directory, timeout, and host access. Its working directory must resolve inside
the project, but the process itself is not sandboxed. It may write files or use
the network. Arguments receive no shell expansion; stdin is closed, output
retention is 32 KiB, and timeout is limited to 1–600 seconds.

See [Verify & Recover](../cli/verification-and-recovery.md) for cancellation,
verification freshness, and checkpoint limitations.

## Approval Artifact

The preview artifact is:

| Field | Value |
| --- | --- |
| `kind` | `preview` |
| `media_type` | `text/x-diff` |
| `metadata.path` | Project-relative target path |
| `parts[0].content` | Unified diff |

Rust extracts this artifact in `progress.rs` and renders it in either a
one-shot terminal diff or fullscreen modal.

## Deny By Default

Agent policies use deny-by-default behavior:

| Agent | Default effect | Important allows |
| --- | --- | --- |
| Architect | deny | delegation and state/history operations |
| Explorer | deny | workspace reads only |
| Coder | deny | workspace writes with approval only |
| Verifier | deny | command execution with approval only |
| Scout | deny | network reads only, when enabled |
| Guide | deny | no tools, no state, no delegation |

This means adding a new tool requires both tool registration and policy review.

## Maintenance Checklist

When adding a tool:

1. Prefer the first-party ProtoLink tool when it provides the required
   capability; put ProtoAgent-specific deterministic filesystem logic in
   `tools.py`.
2. Register the tool only on the agent that needs it.
3. Assign the narrowest capability string.
4. Update that agent's `CapabilityPolicy`.
5. Add or update tests for policy behavior.
6. Update `agent_manifest()` if users should see it.
7. Update this page and `Core / Agent Deck`.
