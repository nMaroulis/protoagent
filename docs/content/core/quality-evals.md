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

## Coding success and same-model comparison (v0.3.0)

The routing harness above does not measure applied coding success. The separate
`coding_eval.py` harness uses disposable workspaces and independent acceptance
scripts to compare the normal deck with a single model-facing agent. Both use the
same selected model, prompt profile, exact-edit tools, frozen checks and bounded
repair policy.

```bash
# Show the matrix; no model calls or execution approvals
proto-cli eval coding --plan --json

# Run one exercise with the selected model, twice: deck and single agent
proto-cli eval coding --live --profile small --task empty-average --json

# Run all three exercises for both architectures
proto-cli eval coding --live --profile small --json
```

Coding evals default to plan mode and the small profile. `--scaffold` also prints
an unscored matrix; it does not simulate solving the exercise. Exercise IDs are
`empty-average`, `whitespace-slug` and `cross-file-flags`. Each live condition gets
fresh fixture files and private runtime storage. The user's project is not edited.
The selected provider/model configuration is reused for model calls.

The fixture bridge approves only allowlisted fixture-file writes and the exact
captured unittest command. The independent acceptance script is held outside the
normal workspace tools. It runs before and after the agent. A successful score
requires an initially failing oracle, a passing final oracle and application
status `completed`. A completed run that fails the oracle is recorded as
`false_completion`; a passing public test suite alone cannot earn success.

JSON retains each architecture's status, oracle outcomes, latency, attempt counts,
available native model-call metrics and context admission diagnostics. Compare
architectures separately; the overall score combines both. Missing provider
usage remains unavailable, rather than being invented as zero. Repeated runs,
model size/quantization, context windows and larger repository tasks should be
recorded before publishing conclusions.

Fixture tests and independent acceptance execute generated code on the host.
Disposable workspaces are not execution sandboxes, and the oracle is not protected
from a malicious host process. The three fixtures establish a reproducible starting
point, not parity with Claude Code or proof that multiple agents outperform one.


Coding comparisons enable Tester for the deck and disable external Scout/MCP access for both conditions. The single-agent condition has no worker delegation. This keeps external server effects and user-wide optional settings out of disposable fixtures. A Tester-on/off ablation is still future evaluation work.
