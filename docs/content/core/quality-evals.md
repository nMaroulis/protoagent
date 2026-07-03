---
title: Quality Evals
description: Prompt-profile benchmark tasks, scaffold checks, live runs, and scoring signals.
---

ProtoAgent includes a small prompt-profile evaluation harness in
`core/protoagent_core/quality_eval.py`. It is designed to compare the
`small`, `medium`, `large`, and `api` prompt profiles against fixed repository
tasks.

## Modes

| Mode | Command | Purpose |
| --- | --- | --- |
| `plan` | `proto-cli eval profiles --plan` | Print the profile/task matrix without running the core. |
| `scaffold` | `proto-cli eval profiles` | Run prompt/context plumbing with `PROTOAGENT_SCAFFOLD=1`; no model calls. |
| `live` | `proto-cli eval profiles --live` | Call the selected model for each task/profile and score actual behavior. |

Live mode passes no interactive progress bridge to the runtime. If Coder
prepares a workspace write, ProtoLink still raises the approval request, but the
Python bridge auto-denies it. This lets the eval measure whether Coder reached
the approval boundary without applying file changes.

## Examples

Run a fast scaffold smoke:

```bash
proto-cli eval profiles --limit 3
```

Run one live profile against one task:

```bash
proto-cli eval profiles --live --profile api --task approval-denial-regression
```

Emit JSON for later comparison:

```bash
proto-cli eval profiles --plan --json
```

List the built-in task set:

```bash
proto-cli eval tasks
```

## Scoring

Each task declares:

| Field | Meaning |
| --- | --- |
| `expected_paths` | Source/docs/test paths the response should discover or touch. |
| `requires_explorer` | The agent should use Explorer for repository evidence. |
| `requires_coder` | The agent should route changes to Coder. |
| `requires_docs` | A docs path should be touched. |
| `requires_tests` | A test path should be touched. |
| `max_changed_files` | Guardrail against broad, unfocused edits. |

The scorer reads normalized `RunEvent`s, approval requests, diff targets, and
response text. It checks for Explorer delegation, Coder delegation or approval
requests, expected path hits, docs/test coverage, and over-edit risk.
Runtime also derives a `RunContract` for each live task. Missing Coder/write
artifacts on a workspace-change task can now produce an `incomplete` run status,
so eval failures in that area indicate runtime enforcement issues as well as
prompt-profile issues.

Scaffold mode marks behavior checks as informational because no real agent
delegation happens. Use live mode when tuning prompt profile quality.
