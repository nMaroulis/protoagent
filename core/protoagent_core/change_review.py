"""Deterministic end-of-run review of native recoverable file effects."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from difflib import unified_diff
from pathlib import Path

from protolink.core.resources import ResourceConflictError
from protolink.tools.builtins.filesystem import FilesystemResource

MAX_FILE_BYTES = 1_048_576
MAX_DIFF_CHARS = 120_000
MAX_FILES = 64


def review_changes(attempt, report):
    """Compare this run's first preimage to its last applied snapshot.

    Previews and denied proposals are not effects. Snapshot chains also keep
    unrelated pre-existing work out of the diff, without depending on Git.
    If another writer interrupted the chain, show individual native edits
    rather than attributing that writer's changes to this run.
    """
    times = {}
    for event in sorted(report.events, key=lambda event: event.timestamp):
        if event.action_id:
            times.setdefault(event.action_id, event.timestamp)
    groups = defaultdict(list)
    for change in attempt.file_changes():
        groups[change.before.revision.resource_id].append(change)
    resources = FilesystemResource([attempt.workspace], max_file_bytes=MAX_FILE_BYTES)
    rows, diffs = [], []
    remaining = MAX_DIFF_CHARS
    for path, changes in sorted(groups.items())[:MAX_FILES]:
        changes.sort(key=lambda change: (times.get(change.action_id, ""), change.change_id))
        applied = [change for change in changes if change.after is not None]
        uncertain = any(
            change.state in {"prepared", "restoring", "uncertain"} for change in changes
        )
        relative = str(Path(path).relative_to(attempt.workspace))
        row = {
            "path": relative,
            "edits": len(applied),
            "change_ids": [change.change_id for change in changes],
            "additions": 0,
            "removals": 0,
            "uncertain": uncertain,
        }
        notes = []
        if not applied:
            if uncertain:
                row.update(
                    kind="uncertain", note="Effect uncertain; inspect the file and /checkpoints."
                )
                rows.append(row)
            continue
        ordered = [
            (times.get(change.action_id, ""), change.before, change.after) for change in applied
        ]
        # Restorations need their own timeline position. Native restored bytes
        # are the original preimage; restored_revision is the new identity.
        for change in applied:
            if (
                change.state == "restored"
                and change.restored_revision is not None
                and change.restore_run_id in attempt.authorization.run_ids
            ):
                restored = replace(change.before, revision=change.restored_revision)
                ordered.append((times.get(change.restore_action_id, ""), change.after, restored))
        steps = [(before, after) for _, before, after in sorted(ordered, key=lambda step: step[0])]
        before, after = steps[0][0], steps[-1][1]
        continuous = all(
            left[1].revision == right[0].revision for left, right in zip(steps, steps[1:])
        )
        unchanged = continuous and before.data == after.data and before.mode == after.mode
        row["kind"] = "unchanged" if unchanged else "created" if before.data is None else "modified"
        if uncertain:
            notes.append("Later effect uncertain; diff covers recorded applied edits only.")
        try:
            row["stale"] = resources.read(path).revision != after.revision
        except (OSError, ValueError, ResourceConflictError):
            row["stale"] = None
            notes.append("Current revision could not be checked; inspect the file.")
        if row["stale"]:
            notes.append("File changed since the recorded edit; diff shows recorded effects.")
        if not continuous:
            notes.append("Other changes interrupted the edits; showing applied steps separately.")
        text = ""
        if not unchanged:
            selected = [(before, after)] if continuous else steps
            for old, new in selected:
                part, note = _snapshot_diff(relative, old, new, remaining - len(text))
                text += part
                body = part.splitlines()[2:]
                row["additions"] += sum(line.startswith("+") for line in body)
                row["removals"] += sum(line.startswith("-") for line in body)
                if note:
                    notes.append(note)
                    break
        if notes:
            row["note"] = " ".join(dict.fromkeys(notes))
        rows.append(row)
        if text:
            diffs.append({"path": relative, "diff": text})
            remaining -= len(text)
    review = {
        "files": rows,
        "file_count": sum(row["kind"] != "unchanged" for row in rows),
        "edit_count": sum(row["edits"] for row in rows),
        "additions": sum(row["additions"] for row in rows),
        "removals": sum(row["removals"] for row in rows),
        "truncated": len(groups) > MAX_FILES,
    }
    review["summary"] = _summary(review, bool(diffs))
    return review, diffs


def _snapshot_diff(path, before, after, remaining):
    if max(len(before.data or b""), len(after.data or b"")) > MAX_FILE_BYTES:
        return "", "File exceeds the 1 MiB diff review limit; inspect it directly."
    try:
        old, new = (
            snapshot.data.decode("utf-8") if snapshot.data is not None else ""
            for snapshot in (before, after)
        )
        if "\0" in old or "\0" in new:
            raise UnicodeError
    except UnicodeError:
        return "", "Binary file; no text diff."
    lines = unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{path}" if before.data is not None else "/dev/null",
        tofile=f"b/{path}" if after.data is not None else "/dev/null",
    )
    parts, size = [], 0
    for line in lines:
        if not line.endswith("\n"):
            line += "\n\\ No newline at end of file\n"
        size += len(line)
        if size > remaining:
            return "", "Combined diff exceeds the review limit; inspect the file directly."
        parts.append(line)
    mode_changed = before.data is not None and after.data is not None and before.mode != after.mode
    return "".join(parts), "File permissions changed." if mode_changed else ""


def _summary(review, has_diff):
    if not review["files"]:
        return "Edits: no recoverable file changes were applied."
    lines = [
        f"Edits: {review['file_count']} file(s), {review['edit_count']} applied edit(s); "
        f"+{review['additions']} / -{review['removals']} displayed lines."
    ]
    for row in review["files"][:12]:
        kind = "uncertain" if row["uncertain"] else row["kind"]
        line = f"- {row['path']}: {kind}"
        if row["additions"] or row["removals"]:
            line += f" (+{row['additions']} / -{row['removals']})"
        if row.get("note"):
            line += f". {row['note']}"
        lines.append(line)
    if len(review["files"]) > 12 or review["truncated"]:
        lines.append("More files in the run report and /checkpoints.")
    if has_diff:
        lines.append("Review the combined applied diff with /diff.")
    lines.append("Command effects outside native file tools are not included in this diff.")
    return "\n".join(lines)
