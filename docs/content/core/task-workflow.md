---
title: Task Workflow
description: Runtime task records, focused worker packets, check plans and small-model execution.
---

ProtoAgent 0.3.0 gives each model a focused job and keeps progress in application
state. `TaskRecord` owns the objective, acceptance criteria, allowed file paths,
available and selected check IDs, source references and worker reports. It
survives conversation compaction and request-level observation eviction.

## Worker responsibilities

| Role | Responsibility |
| --- | --- |
| Architect | Choose focused tasks and explain the final outcome. |
| Explorer | Gather bounded repository evidence. |
| Tester | Propose regression cases, select existing check IDs and diagnose failures; read-only. |
| Coder | Read assigned spans and prepare approved file changes. |
| Verifier | Execute approved commands without an LLM. |
| Scout | Optional public-web evidence, disabled by default. |
| MCP | Optional tool-only external evidence, disabled by default; [setup](mcp.md). |

Tester is optional, enabled by default, and is not a mandatory model call for every task. Disable it with `agents tester off` or `/agents tester off`; Architect/Coder then handle criteria and regressions while Verifier stays required.
Architect is instructed to use it for behavior changes. Tester cannot write tests
or execute commands; Coder implements proposed regression tests and Verifier
measures the results. A Tester report is advice, never completion evidence.

## Task tools

Architect's `task_status()` reads the record. `plan_task(paths, criteria, check_ids)`
selects checks and a write scope before effects. The default record uses the
original objective and all discovered checks when no narrower plan is submitted.
Once a write or final check is proposed the plan is frozen for the run, including
repairs. A plan cannot remove checks after observing a failure.

`worker_packet(role, objective, paths)` resolves up to eight real source references
into bounded source spans (6000 source characters shared across references),
criteria and check IDs. Source reads may include supporting files outside the
write scope; writes remain restricted. Architect supplies this packet
through normal ProtoLink delegation. Workers can reread source directly and use
`report_task(status, summary, missing_paths)` with `done`, `needs_context` or
`blocked`. The runtime record is exposed in run output and refreshed in admitted
model requests. Reports never replace executed receipts. An unresolved `needs_context` or `blocked`
report prevents completion until the worker reports a resolved outcome. Explicit
read-only user requests deny file writes.

## Repository check configuration

Before inference the application snapshots `.protoagent/project.json` if present.
Its checks take precedence over discovery. Coder cannot edit that configuration
inside the run. Each command still requires native approval.

```json
{
  "checks": [
    {
      "id": "regression",
      "argv": [".venv/bin/python", "-m", "pytest", "tests", "-q"],
      "cwd": ".",
      "env": {},
      "paths": ["tests", "src"]
    }
  ]
}
```

Executables must be absolute, workspace-relative paths, or discoverable through
an explicitly configured `env.PATH`. Environments are not inherited. No provider
credentials are copied. Keep dependency paths focused; dependency inventory is
limited to 512 files. Relative paths resolve beneath the selected workspace.

Without a project configuration, the runtime discovers Python unittest suites in
`tests/` or `core/tests/`, root Cargo tests, and npm `test`, `typecheck`, `lint` and
`build` scripts. Discovery reads manifests and paths without importing project
code or executing commands. For pytest projects, custom commands, monorepos and
required environment settings, use explicit project configuration.

## Baseline, editing and final verification

1. Inspect source and choose criteria/check IDs.
2. Call `run_check(check_id, phase="baseline")` to reproduce existing behavior.
   Baseline failure is evidence and does not close the edit phase.
3. Perform approved edits. `execute_command` preparation, such as creating a
   directory, also permits later edits unless it exactly matches a selected
   repository check (use `run_check(..., phase="baseline")` for that case).
4. Call `run_check(check_id, phase="verify")` for every selected check.
   Final verification closes the edit phase for this attempt.
5. After completed failing final checks, Graph may start at most two repairs.
   Canceled, denied, timed-out and uncertain effects stop autonomous repair.

Commands have the same approval, cancellation, environment and output limits as
native ProtoLink execution. `run_check` expands a check ID into its exact frozen
command before approval; the model does not regenerate argv and environment.
An unrelated exit-zero command does not count as verification.

## Completion evidence

Run output separates `applied`, `verified` and `criteria_supported`:

- Applied requires an executed native file change at its current revision.
- Verified requires successful final executions of all selected checks, tied to
  this run's changed files, read sources and declared check dependencies.
- Criteria supported means the application's required evidence is satisfied.
  Passing the configured checks does not prove every requested semantic property.

Code changes without a qualifying verification plan remain incomplete. Explicit documentation-only requests whose writes are
limited to `.md`, `.rst` and `.txt` files may complete with verification reported
`unverified`. Baseline results cannot satisfy final verification. Source changes
after a final check make evidence stale. Dependency coverage is explicit, not a
snapshot of every resource in the repository.

## Small-model source editing and context

`read_file(path, start_line, end_line)` returns one source span, a full-file SHA-256
revision, and pagination metadata. Small-profile workers return at most 4000
characters per read; other workers default to 8192. Coder receives source without
line-number prefixes so copied old text can match exactly. Explorer and Tester
retain numbered evidence. Large files can be read in
spans, up to an 8 MiB file ceiling. A partial long line is marked explicitly.

`edit_file(path, old, new, expected_revision)` replaces exactly one occurrence.
Ambiguous source or a stale revision fails before approval. It preserves other
source and line endings, then uses native ProtoLink replacement for its diff,
preimage, approval, cancellation and recovery. New files still use `create_file`;
whole-file `replace_file` remains available for necessary complete replacements.

Per-request admission estimates instructions, tool declarations, agent cards,
history and evidence together and reserves output space. Older observations are
evicted as complete messages, preserving native tool exchanges and the current
task. The runtime record is reinserted. If mandatory input cannot fit, the request
fails explicitly. These are estimates, not exact provider-token guarantees.
For small profiles without a known model window, the runtime uses an 8192-token
application admission cap. Configure the provider's `context_window` when its
actual limit is known; the fallback is not a measurement of model capacity.

The default architecture is sequential. This release does not claim a measured
performance advantage over a single agent; use the coding evals to test that
hypothesis on your selected model.

## Compact protocol for the small profile

The small profile replaces ProtoLink's long model-facing protocol template using
its public prompt builder. It keeps a short action-format guide, role instructions,
agent identity, exact tool input schemas and compact worker cards. Large output
schemas, repeated examples and long descriptions are omitted from the prompt.
Native providers continue to receive native tool declarations. Native validation,
approval, process execution and recovery are unchanged.

Agent cards already appear in the system message, so admission does not count a
second copy. JSON tool schemas are counted in their prompt text; native schemas
are counted as the additional provider payload. Integration tests exercise actual
JSON/native streaming and delegation under an 8192-token admission cap. This is
mechanical context validation, not a live-model coding quality result.

ProtoLink 0.7.4 also bounds each individual inference loop to ten model actions;
shared run budgets are separate. Focused jobs can use the default task record and
Coder's direct reads to avoid redundant planning/status calls. For a project with
many mandatory commands, a project-owned check script captured as one check ID
can run the combined validation. The model cannot invent that script or change its
specification during the run. Larger automatic check stages remain future work.
