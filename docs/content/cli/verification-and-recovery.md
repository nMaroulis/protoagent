---
title: Verify & Recover
description: Inspect exact command approvals, native execution evidence, and recoverable file changes.
---

## Edit → verify → inspect

```text
> Fix the parser and run the relevant tests.
```

Architect gathers repository evidence, delegates edits to Coder, then calls
Verifier's native `execute_command` tool. Each command requires a separate
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
environment**, not inherited credentials or PATH. Use an absolute executable
or explicitly supply the PATH needed by the command. ProtoLink resolves and
freezes the execution specification before approval. Registration runs nothing.

The local backend executes on the host, with the user's permissions. It can
write outside the project or use the network, including with Scout disabled.
A project working directory does not provide sandbox isolation. Standard input
is closed. The application sets ceilings of **600 seconds** and **32 KiB combined
stdout/stderr**. Architect is instructed to pass all limits explicitly, usually
120 seconds; ProtoLink's tool default is 60 seconds. Native runtime budgets may
stop execution sooner. Truncation, timeout, cancellation and duration remain in
the structured result.

Esc/Ctrl-C goes through `RunHandle.cancel()`. ProtoLink cleans up POSIX process
groups on cancellation, timeout and normal parent exit. Deliberately detached
processes can escape this cleanup. Command effects have no automatic rollback.

## Completion and repairs

A write task needs an **executed native file change at its current revision**.
A proposed diff, approval, delegation or model claim cannot satisfy that check.
A requested verification needs an actual executed command.

| Verification status | Meaning |
| --- | --- |
| `passed` | Latest recorded executions passed and their recorded resource revisions are current. |
| `failed` | A recorded command failed or its execution evidence cannot be accepted. |
| `stale` | A resource referenced by a check changed. |
| `unverified` | No test/build command was executed. |

Checks reference the native revisions of files changed by this run, captured
when a command is proposed. An external edit to those files also invalidates
the check. This does not track every repository input, dependency or file a
command can modify. A passing exit status is evidence for that command, not a
proof that all behavior is correct. A write can be applied but remain unverified.

All edits happen before checking within an attempt. Once a command is proposed,
further file mutations are denied for that attempt. A native **Graph** permits
one initial attempt and at most **two repair attempts** after completed nonzero
checks. Its limits are enforced in code and share native workflow budgets.
Denials, stale revisions, timeouts, interrupted effects and missing evidence
stop repair routing. Transport retries are separate; task submissions are never
automatically replayed after a lost response.

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
