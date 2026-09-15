"""Application acceptance predicates over ProtoLink execution receipts and revisions."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Any

from protolink import CompletionCheck, CompletionValidator, ResourceRevision, ValidationResult
from protolink.tools.builtins.filesystem import FilesystemResource


@dataclass
class Acceptance:
    """Application-facing projection of native validation and measured commands."""

    completion: dict[str, Any]
    verification: dict[str, Any]
    repairable: bool = False


async def validate_completion(contract, task, report, attempt, broker) -> Acceptance:
    """Validate actual effects; previews, approval and model claims are insufficient.

    A repair is eligible only for a completed, nonzero command in this attempt.
    Denials, interrupted effects, timeouts, cancellations, missing evidence and
    stale revisions stop the workflow and require inspection or a new instruction.
    """
    resources = FilesystemResource([attempt.workspace])

    def read_revision(path):
        return resources.read(path).revision

    # The complete native stream also contains model action annotations whose
    # payload.action is a string. Only prepared-action receipts prove effects.
    receipts = sorted(
        (
            event
            for event in report.events
            if event.type == "action.completed" and isinstance(event.payload.get("action"), dict)
        ),
        key=lambda event: event.timestamp,
    )
    checks = []
    write_events = [
        event
        for event in receipts
        if event.payload.get("action", {}).get("name") in {"create_file", "replace_file"}
    ]
    # Only the latest applied edit to each path describes the final resource.
    latest_writes = {}
    for event in write_events:
        result = event.payload.get("result")
        if isinstance(result, dict) and result.get("state") == "applied" and result.get("resource"):
            latest_writes[result["resource"]["resource_id"]] = event
    if contract.requires_write:
        if not latest_writes:
            checks.append(CompletionCheck("applied workspace change", lambda evidence: False))
        for path, event in latest_writes.items():
            revision = ResourceRevision(**event.payload["result"]["resource"])
            checks.append(
                CompletionCheck(
                    f"applied change: {path}",
                    lambda evidence: True,
                    action_ids=(event.action_id,),
                    revisions=(revision,),
                    read_revision=read_revision,
                )
            )
    commands = []
    latest_commands = {}
    for event in receipts:
        action = event.payload.get("action", {})
        if action.get("name") != "execute_command":
            continue
        result = event.payload.get("result")
        if not isinstance(result, dict) or "exit_code" not in result:
            continue
        args = action.get("payload", {}).get("arguments", {})
        command = shlex.join(args.get("argv", []))
        item = {
            **result,
            "action_id": event.action_id,
            "command": command,
            "cwd": args.get("cwd", ""),
            "attempt": attempt.command_attempts.get(event.action_id, 0),
        }
        commands.append(item)
        # A newer execution of the same command replaces its failed older evidence.
        key = (command, item["cwd"], tuple(sorted(args.get("env", {}).items())))
        latest_commands[key] = item
    if contract.task_kind == "workspace-verification" and not commands:
        checks.append(CompletionCheck("executed verification", lambda evidence: False))
    for index, item in enumerate(latest_commands.values()):
        action_id = item["action_id"]
        successful = item["exit_code"] == 0 and not any(
            item.get(key) for key in ("timed_out", "canceled", "budget_exceeded")
        )
        if action_id not in attempt.command_revisions:

            def predicate(evidence):
                return ValidationResult("check", "blocked", "revision_evidence_missing")
        else:

            def predicate(evidence, passed=successful):
                return passed

        checks.append(
            CompletionCheck(
                f"command {index + 1}: {item['command']}",
                predicate,
                action_ids=(action_id,),
                revisions=attempt.command_revisions.get(action_id, ()),
                read_revision=read_revision,
            )
        )
    if not checks:
        checks.append(
            CompletionCheck(
                "answer produced",
                lambda evidence: bool(evidence.task.get_last_part_content()),
                require_execution=False,
            )
        )
    results = await CompletionValidator(checks).validate(task, report=report)
    validations = [result.to_dict() for result in results]
    statuses = [result.status for result in results]
    uncertain_changes = attempt.has_uncertain_changes()
    stopped = (
        attempt.denied
        or bool(task.metadata.get("blockers"))
        or any(
            record.status != "approved" for record in broker.records(attempt.authorization.scope)
        )
        or any(
            event.type in {"action.denied", "action.failed", "budget.exceeded", "workflow.blocked"}
            for event in report.events
        )
    )
    native_status = task.state.value
    outcome = "satisfied" if all(result.passed for result in results) else "incomplete"
    if native_status in {"failed", "canceled", "input_required"} or stopped:
        outcome = "canceled" if native_status == "canceled" else "blocked"
    if uncertain_changes:
        outcome = "uncertain"
    missing = [
        f"{result.name}: {result.code or result.status}" for result in results if not result.passed
    ]
    if stopped:
        missing.append("A native denial, blocker or failed action requires a new instruction.")
    if uncertain_changes:
        missing.append(
            "A recovery record has an uncertain effect; inspect it before requesting new work."
        )
    current_results = [item for item in latest_commands.values()]
    command_validations = [value for value in validations if value["name"].startswith("command ")]
    for item, validation in zip(current_results, command_validations, strict=True):
        item["validation"] = validation
        item["stale"] = validation["status"] == "stale"
    verification_status = (
        "unverified"
        if not commands
        else (
            "stale"
            if any(item.get("stale") for item in current_results)
            else "passed"
            if command_validations
            and all(value["status"] == "passed" for value in command_validations)
            else "failed"
        )
    )
    repairable = (
        contract.requires_write
        and outcome == "incomplete"
        and not stopped
        and all(status in {"passed", "failed"} for status in statuses)
        and bool(current_results)
        and any(
            item["attempt"] == attempt.attempt and item["exit_code"] not in {None, 0}
            for item in current_results
        )
        and not any(
            item.get(flag)
            for item in current_results
            for flag in ("timed_out", "canceled", "budget_exceeded", "stale")
        )
    )
    return Acceptance(
        {
            "outcome": outcome,
            "satisfied": outcome == "satisfied",
            "checks": validations,
            "message": "Execution evidence satisfies the application checks."
            if outcome == "satisfied"
            else " ".join(missing) or f"Run is {outcome}.",
            "missing": missing,
        },
        {
            "status": verification_status,
            "results": commands,
            "latest": current_results,
            "attempts": attempt.attempt,
        },
        repairable,
    )


def verification_summary(report: dict[str, Any]) -> str:
    """Render native exit statuses and resource-dependent acceptance results."""
    lines = [f"Verification: {report['status']}."]
    if not report["results"]:
        return lines[0] + " No test/build command was executed."
    for item in report.get("latest", report["results"]):
        outcome = "timeout" if item.get("timed_out") else f"exit {item['exit_code']}"
        stale = " (resource changed since this check)" if item.get("stale") else ""
        lines.append(f"- {item['command']}: {outcome}{stale}")
    return "\n".join(lines)
