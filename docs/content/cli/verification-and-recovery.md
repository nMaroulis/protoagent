---
title: Verify & Recover
description: Inspect exact command approvals, native execution evidence, and recoverable file changes.
---

## Edit → verify → inspect

```text
> Fix the parser and run the relevant tests.
```

Architect gathers repository evidence, delegates edits to Coder, then calls
Verifier's `run_check(check_id, phase)` tool, prepared as native `execute_command`.
Check IDs and specifications are captured from the project before model execution
(see [Task Workflow](../core/task-workflow.md)). Each command requires a separate
`process.execute` approval. Press **V** to inspect its argv, absolute working
directory, explicit environment, timeout, output limit and host execution
boundary. Scroll with arrows or PageUp/PageDown; return and press **Y** to
approve or **N/Esc** to deny. The one-shot CLI prints the same preview.

```json
{
  "argv": ["/your/project/.venv/bin/python", "-m", "pytest", "tests/test_parser.py", "-q"],
  "cwd": "/your/project",
  "env": {},
  "timeout_seconds": 120,
  "max_output_bytes": 32768
}
```

Arguments receive no implicit shell expansion. `env: {}` means an **empty
environment**, not inherited credentials or PATH. Relative `cwd` resolves from
the selected project. Bare program names resolve through explicit `env.PATH`
or a fixed application system search path; `python`/`python3` without PATH use
project/runtime interpreter selection. This resolves only the executable and
does not add PATH to the child. Supply any environment required by the program.
The exact absolute executable, directory, environment and limits enter native
preparation and approval. Registration runs nothing.

The local backend executes on the host, with the user's permissions. It can
write outside the project or use the network, including with Scout disabled.
A project working directory does not provide sandbox isolation. Standard input
is closed. The application sets ceilings of **600 seconds** and **32 KiB combined
stdout/stderr**. Architect is instructed to pass all limits explicitly, usually
120 seconds; ProtoLink's tool default is 60 seconds. Native runtime budgets may
stop execution sooner. Truncation, timeout, cancellation and duration remain in
the structured result.

ProtoAgent's command adapter defaults to 60 seconds and 8192 output bytes, below
its native ceilings. Unknown executables, invalid limits or nonexistent working
directories receive no-change feedback before dispatch in the small local
interface, allowing bounded input correction. Denied approvals and failures
after dispatch remain stops.

Moving root unittest tests into a folder requires an `__init__.py` package marker
for the existing root runner to recurse into it. Approved `mkdir`/`mv` commands
prepare the layout; Coder applies any package/import edits with their own diffs.
These host commands have no automatic recovery or file diff. Final verification
must still execute the selected check and find real tests.

Esc/Ctrl-C goes through `RunHandle.cancel()`. ProtoLink cleans up POSIX process
groups on cancellation, timeout and normal parent exit. Deliberately detached
processes can escape this cleanup. Command effects have no automatic rollback.

## Completion and verification

A write task needs an **executed native file change at its current revision**.
A proposed diff, approval, delegation or model claim cannot satisfy that check.
Verified code changes need the enabled selected repository checks. Disabled or
unavailable checks leave applied edits unverified.
Unrelated successful commands do not qualify. Explicit documentation-only requests can
complete with `applied=true` and `verified=false`.

| Verification status | Meaning |
| --- | --- |
| `passed` | Latest recorded executions passed and their recorded resource revisions are current. |
| `failed` | A recorded command failed or its execution evidence cannot be accepted. |
| `stale` | A resource referenced by a check changed. |
| `unverified` | No qualifying final repository check was executed. |
| `disabled` | Verifier was switched off; no check discovery or command ran. |
| `not_configured` | No runnable repository checks were available. |
| `unavailable` | The selected Python runner or an external dependency is missing. |
| `deferred` | You explicitly chose to handle verification. Any previous failure remains recorded in `last_attempt_status` and command receipts. |

Checks reference native revisions of changed files, worker-read sources and
configured dependencies, bounded to 512 files and captured at preparation.
An external edit to those files also invalidates
the check. This does not track every repository input, dependency or file a
command can modify. A passing exit status is evidence for that command, not a
proof that all behavior is correct. A write can be applied but remain unverified.

The harness does not ask to install missing test dependencies or retry failed checks.
If an editing clarification is awaiting your response, you may explicitly answer **"don't run it, I'll do it"** or **"I'll verify it myself; stop here"**.
After edits or final checking have begun, this stops immediately with no further
commands, model calls or repairs. The application status is `unverified`, and
the response lists applied files separately from the last check's result.
Checks are neither deleted from the frozen plan nor relabeled as passed;
`satisfied=false` and `verified=false`. Uncertain effects, denial and stale
revisions still require inspection. Esc skips the question without creating a
manual verification decision.

For Python projects with no checks, a predefined `python-tests` runner can execute
useful new root `test_*.py` unittest regressions. Test creation is not an admission
requirement for edits. Without runnable tests, the harness skips an empty bootstrap
and reports unverified changes. Verifier runs available checks with native approval. Zero tests
reports missing verification, even if Python exits successfully. Invalid planning
IDs receive native no-change feedback for correction within the shared allowance
(default two per inference). Rejected plans or check requests cannot provide
verification evidence. Explicit project
check configuration takes precedence. See [Task Workflow](../core/task-workflow.md).

The harness runs one check pass after editing. No default baseline, model-managed
check sequence or automatic repair loop is used. A follow-up request can address
a real failure. `/agents verifier off` (shell: `proto-cli agents verifier off`)
skips discovery and commands; on restores the pass. Tester is independent and
opt-in for new configurations. Explicit test requests run without a selected or
running model. The plan freezes at the first write or final check; native
approvals, revisions, cancellation and the shared root budget still apply.

The report retains every native receipt and each `validation.completed` result.
A newer execution replaces older evidence only for the same argv, directory
and environment in the displayed command summary. Inspect `failed`, `blocked`,
`canceled`, `incomplete` or `uncertain` runs before requesting new work.

## Recover a file

```text
/checkpoints
/undo
/undo <change-id>
```

```bash
proto-cli checkpoints
proto-cli undo
proto-cli undo <change-id>
```

Coder registers ProtoLink's `create_file`, `replace_file`, `preview_change` and
`restore_change`. Both `filesystem.write` and `filesystem.restore` require
approval. Native tools save original bytes and mode before mutation, including
preexisting uncommitted content. Undo needs no model: `/undo` resolves the latest
applied change before asking for approval of its reverse diff.

Restoring a new file removes it; restoring a replacement restores its original
bytes and mode. A changed revision causes a conflict, even if the contents look
similar. An approval-time edit also invalidates a prepared write or restoration.
**Chained undo is conservative:** restoring the latest edit changes the file's
identity, so an older checkpoint for that same file may conflict. Reconcile it
manually; do not force an overwrite.

`/checkpoints` shows the latest 50 native records with their states, plus retained
legacy records. `prepared`, `restoring` and `uncertain` require inspection: the
effect may already have happened. ProtoAgent does not replay them. Applied
records remain subject to a fresh revision check when restoration is requested.

## Storage and platform boundaries

Native recovery lives under `${PROTOAGENT_CONFIG_DIR:-~/.protoagent}/recovery/`.
Each canonical project has a dedicated `StorageCheckpointStore` over
`SQLiteStorage`; one live writer holds a project lease. Storage directories use
mode 0700 and files use 0600. Native run snapshots, reports and broker records
are stored separately under `runs/`. Known configured credentials are redacted
from output and reports; arbitrary secrets printed by project code may remain.
Approval and recovery storage contains sensitive original data and stays private.

Recoverable filesystem tools require **POSIX**, absolute paths under explicit
allowed roots, existing parent directories and no symlink components. Native
new files default to mode 0600. Creating directories requires a separately
approved host command and a later edit run. Recovery covers individual Coder
changes, not Git state, whole tasks or command side effects. `/context reset`
does not delete recovery records.

The old **v0.2.1** database under `checkpoints/` is preserved unchanged. Its
entries appear as `legacy` and are inspection-only: they lack native resource
revisions and cannot be safely imported as executed ProtoLink changes.
