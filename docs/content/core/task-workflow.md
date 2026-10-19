---
title: Task Workflow
description: Lightweight editing, one native check pass, and truthful completion evidence.
---

ProtoAgent keeps model work focused on understanding and editing. Verification is
software scheduling: **Architect → Coder → one enabled check pass → result**.
A missing test runner never prevents the requested edits from starting. No
baseline, model-managed test sequence, install question or automatic repair loop
is part of the default workflow.

RunContract routes greetings without tools or discovery, repository questions to
read-only evidence, editing to Coder, and explicit test requests directly to the
check pass. An explicit verification request needs no model inference.

## Worker responsibilities

Architect inspects relevant evidence, resolves blocking requirements and assigns
concrete edits. Coder reads and edits within the approved scope, adding useful
regressions in the existing style. Neither chooses verification commands.

Tester is optional test-design assistance and defaults **off for new configs**.
Existing saved settings are preserved. It is useful for difficult regressions;
it is never a mandatory stage or a source of execution evidence.

Verifier remains a real ProtoLink agent with native process tools and **no LLM**.
The harness calls it through AgentGroup after editing; check tools are not offered
to the coding model. Its `execute_command` tool can still support necessary,
approved preparation such as mkdir or mv. Preparation commands do not certify
verification or close the edit phase.

Use `/agents verifier off` (shell: `proto-cli agents verifier off`) to skip check
discovery and commands on subsequent runs. Applied edits then remain `unverified`.
`/agents verifier on` restores the check pass. `/agents tester on|off` controls
optional test design independently. Architect, Explorer and Coder remain required.

## Task tools

TaskRecord retains the objective, criteria, file scope, source references, answered
clarifications and worker outcomes outside prunable model history. `plan_task`
can narrow paths and criteria before the first write. Omit `check_ids` to preserve
the captured owner checks. Unknown IDs receive bounded native no-change feedback;
command text is never a check ID. The first write or final check freezes the plan.

`worker_packet` is a low-level context helper, not execution. Small local runs
use native typed worker calls such as `read_file(path)` and `coder(task)` instead
of a separate packet stage. Disabled workers have no route.

## Repository check configuration

Bounded static discovery reads project manifests and existing tests without
importing project code or installing anything. Function-style or mixed Python
suites select pytest; unittest-only suites use the standard-library runner.
Project Python selection prefers the project environment and then the available
runtime interpreter. Discovery does not prove that dependencies are installed.

Owners can declare exact checks in `.protoagent/project.json`:

```json
{
  "checks": [
    {
      "id": "tests",
      "argv": ["/absolute/path/to/python", "-m", "pytest", "tests", "-q"],
      "cwd": ".",
      "env": {},
      "paths": ["src", "tests", "pyproject.toml"]
    }
  ]
}
```

An explicit configuration, including an empty check list, takes precedence over
discovery. Captured IDs, argv, cwd, env and revision dependencies stay fixed during
the run. A Python bootstrap runner is available for useful newly added root
`test_*.py` tests; it is not executed when no such tests exist. The harness never
requires a new test file just to admit an otherwise valid source edit.

## Editing and final verification

1. Inspect relevant source and apply approved edits.
2. Resolve unfinished worker assignments within the existing bounded budget.
3. The harness runs each selected check once, using native exact command approval.
4. Return the edit summary, diffs and one concise verification line.

Checks never dispatch another model call or open an automatic repair phase.
A real regression is reported as failed/incomplete with details in `/trace`;
a follow-up request can fix it. Missing Python runners or external dependencies
produce `unavailable`, preserve applied changes as unverified, and trigger no
installation question or retry. Missing local project modules remain real failures.
No usable checks produce `not_configured`, not a claim of success. Zero collected
tests do not establish verification.

Disabling Verifier, declining a command, cancellation and timeouts do not count
as passing checks. Native denials, uncertain effects and stale revisions remain
visible. A validated manual-verification stop after editing prevents the check
pass and preserves the edits and native evidence.

### Command preparation and moving tests

Commands retain exact argv, absolute cwd, explicit env and bounded output/time.
Bare executables and project-relative cwd resolve before approval; no shell or
parent environment is inherited. Viewing command details is separate from
approving execution. Commands run with host permissions, without sandbox isolation.

Directory creation and file moves use approved preparation commands. With root
unittest discovery, Coder may need package markers and import updates for tests
moved into a subdirectory. Such preparation does not replace the final check.

## Completion evidence

Native applied-write receipts and current resource revisions prove actual file
changes. Approval, previews, plans and model prose do not. Enabled passing checks
support completion; failed, missing, stale or skipped checks never become passes.
Documentation-only changes may finish without running a suite and remain unverified.
Combined applied diffs remain reviewable even after failure or a manual stop.

## Small-model source editing and context

Bounded reads provide source and a SHA-256 revision. Exact edits require a unique
observed span, a matching revision and changed text. The native preparation
correction loop rejects stale selections or newly introduced Python syntax
errors before approval, execution and tool-call accounting. No model or executable
validation action is needed. Native recoverable writes preserve preimages and
approved afterimages; subsequent edits may use the returned source revision.

Local Coder work uses ProtoLink checkpointed slices of nine steps. Fresh applied
edits at current revisions can justify up to three progressing slices; root
budgets, correction allowances and committed receipts remain cumulative. A slice
without fresh edits stops. Approval/input resumes also begin bounded slices, so
this is not a fixed 27-call allowance. `PROTOAGENT_CHECKPOINTED_WORKERS=0` selects
the live fallback; explicit transport meshes already use it.

Execution checkpoints are private temporary files for the active workflow.
TaskRecord and Graph state are live application state, so this does not support
restarting unfinished coding after process exit. Up to two focused Graph
continuations may finish progressing partial edits before any final check.

## Compact protocol for the small profile

Native `Agent.add_worker_tool` supplies narrow, typed worker routes. The model
sees the calls needed for source work and optional evidence, without baseline or
final-check schemas. ProtoLink owns action parsing, preparation corrections,
supervision, approvals, live child streaming and execution receipts. Software
schedules verification and presents the measured result after the edit response.
