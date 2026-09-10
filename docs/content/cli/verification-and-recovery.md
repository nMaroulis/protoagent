---
title: Verify & Recover
description: Approve real test commands, inspect measured results, and undo Coder changes.
---

## Edit → verify → inspect

Ask for the change and its acceptance check in one prompt:

```text
> Fix the parser and run the relevant tests.
```

Architect gathers repository evidence through Explorer, delegates edits to
Coder, then invokes the tool-only **Verifier** through ProtoLink. It is instructed
to use the project's actual test/build/lint command and make at most two focused
repair attempts after failures. This retry limit is a prompt instruction; the
ProtoLink run budget and each command's timeout bound execution.

Each command pauses for its own `shell.execute` approval. Press **V** in the TUI
approval modal to inspect the complete command, directory, and timeout; scroll
with arrow keys or PageUp/PageDown. Return to the approval screen and press
**Y** to run or **N/Esc** to deny. The one-shot CLI prints the same preview.

```text
Command preview
python -m pytest tests/test_parser.py -q
Directory: /your/project
Timeout: 120s
Runs project code with host access; may write files or use the network.
```

Arguments are passed directly without shell expansion. Commands inherit the
host environment and permissions. A project working directory is **not a
sandbox**: approved project code can write elsewhere or access the network,
including when Scout is off. Standard input is closed. Timeout is 120 seconds
by default, adjustable by the tool call from 1 to 600 seconds. Combined output
is streamed with a 32 KiB retention limit and terminal escape filtering.

Esc/Ctrl-C requests ProtoLink task cancellation. On POSIX, cancellation and
timeout kill the command's process group; on other platforms the direct child
is killed. Processes that deliberately detach are outside that guarantee.

## Read the result

The final answer includes measured verification evidence for change tasks:

| Status | Meaning |
| --- | --- |
| `passed` | Latest execution of each recorded command passed for the current set of agent changes. |
| `failed` | A current command failed to launch, timed out, or exited nonzero. |
| `not-run` | No recorded command covers the current set of agent changes. |

A later Coder write or restore invalidates earlier checks. A successful retry
replaces the earlier result for the same command and directory in the summary;
both executions remain in the report. This tracks Coder mutations within the
run, not external editors or every file a command might modify. A passing
command says only that the command exited successfully, not that all behavior
is correct. Results are emitted as ProtoLink `verification.result` events and
included in `RunReport.metadata.verification`.

## Recover a file

Every changed file written by Coder receives a checkpoint before its bytes are
replaced. Checkpoints retain the previous bytes and permission bits, including
uncommitted content that existed before the agent edit.

```text
/checkpoints
/undo
/undo <checkpoint-id>
```

Shell equivalents:

```bash
proto-cli checkpoints
proto-cli undo
proto-cli undo <checkpoint-id>
```

`/checkpoints` lists the latest 50 active snapshots for the selected project.
`/undo` selects the latest snapshot and shows the reverse diff. Selection is
fixed before approval. Recovery uses Coder's ProtoLink `workspace.write` tool
and approval policy and needs no model or API key. Undoing a newly created file
removes it; undoing a replacement restores its original content and mode.

If the file changed after the agent write, undo refuses to overwrite it. Review
and reconcile that file manually before trying recovery. File writes also
recheck their preimage after approval, so an old preview cannot silently
overwrite an edit made while the approval screen was open.

Snapshots live in a project-specific SQLite ledger under
`${PROTOAGENT_CONFIG_DIR:-~/.protoagent}/checkpoints/`. They cover individual
Coder writes, not entire tasks, Git state, or files modified by commands.
Undo related writes in reverse order. Snapshots are separate from ProtoLink
conversation memory and survive `/context reset`. An interrupted or failed
write can leave a snapshot whose content check prevents restoration.
