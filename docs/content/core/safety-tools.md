---
title: Safety And Tools
description: Workspace-safe tools, path boundaries, diff previews, and authorization.
---

The core tools are deterministic helpers exposed to Explorer and Coder through
ProtoLink tool registration.

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
| `build_context_map(max_files=80)` | Compact file list plus git status. |

Ignored directories:

```text
.git .hg .svn .venv __pycache__ node_modules target dist build
```

Common binary suffixes are skipped by search and indexing.

## Write Tools

Coder uses these through approval-gated tools:

| Tool | Purpose |
| --- | --- |
| `generate_unified_diff(path, updated_content, original_content=None)` | Preview a file replacement. |
| `create_new_file(path, content)` | Preview a new file. |
| `write_file(path, content, overwrite=True)` | Execute only after authorization. |

The tool exposed to the model is not a raw filesystem write. The Coder factory
wraps it in an action builder that first creates a `RunAction` with a diff
artifact. ProtoLink policy pauses before `write_file()` runs.

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
| Explorer | deny | workspace reads and state/history operations |
| Coder | deny | workspace writes with approval plus state/history operations |
| Guide | deny | no tools, no state, no delegation |

This means adding a new tool requires both tool registration and policy review.

## Maintenance Checklist

When adding a tool:

1. Put deterministic filesystem logic in `tools.py`.
2. Register the tool only on the agent that needs it.
3. Assign the narrowest capability string.
4. Update that agent's `CapabilityPolicy`.
5. Add or update tests for policy behavior.
6. Update `agent_manifest()` if users should see it.
7. Update this page and `Core / Agent Deck`.
