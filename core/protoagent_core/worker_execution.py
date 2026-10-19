"""Live application policy around ProtoLink's checkpointed local work slices.

Only execution cursors are checkpointed. The coding Graph and TaskRecord remain
live application state, so these private temporary stores never enable restart.
"""

from contextlib import contextmanager, suppress
from tempfile import TemporaryDirectory

from protolink import ApprovalRequest, RunContext, RunHandoff, RunReport, SQLiteDurableStore
from protolink.core.durable import durable_streaming_scope
from protolink.core.execution import publish_runtime_event
from protolink.tools.builtins.filesystem import FilesystemResource
from protolink.tools.builtins.user_input import UserInputRequest

from .worker_budget import applied_writes, coder_work_windows


@contextmanager
def checkpointed_workers(parent, context):
    """Scope checkpoint bytes to one live workflow and preserve remote mode."""
    import os

    coder = parent.subagents.get("coder")
    if coder is None or os.getenv("PROTOAGENT_CHECKPOINTED_WORKERS", "1") == "0":
        yield False
        return
    original = parent.durability, coder.yield_after_steps
    with TemporaryDirectory(prefix="protoagent-worker-slices-") as directory:
        parent.durability = SQLiteDurableStore(f"{directory}/execution.sqlite")
        coder.yield_after_steps = 9
        context.metadata["native_worker_slices"] = True
        try:
            yield True
        finally:
            parent.durability, coder.yield_after_steps = original
            context.metadata.pop("native_worker_slices", None)


async def continue_workers(
    parent, task, *, attempt, broker, user_input_handler=None, event_handler=None
):
    """Grant only safe, progressing slices; all execution/resume stays in the SDK."""
    from protolink import ResourceRevision, RunEvent

    windows, seen = {}, {}
    application_events = []

    async def emit(event):
        application_events.append(event.to_dict())
        await publish_runtime_event(event)
        if event_handler:
            # Presentation is not an execution or acceptance authority.
            with suppress(Exception):
                event_handler(event)
        records = task.metadata.setdefault("run_events", [])
        if not any(item["event_id"] == event.event_id for item in records):
            records.append(event.to_dict())

    while task.state.value == "input-required" and parent.durability is not None:
        pending = task.metadata.get("interruption", {})
        root_run_id = RunContext.from_task(task).run_id
        if attempt.denied or attempt.has_uncertain_changes():
            return task
        response = {}
        if pending.get("kind") == "yield":
            handoff = RunHandoff.from_task(task)
            if handoff is None:
                return task
            child = parent.durability.get(handoff.run_id)
            if child is None or child.agent_name != "coder":
                return task
            report = RunReport.from_task(task)
            writes = applied_writes(report, handoff.run_id)
            previous = seen.setdefault(handoff.run_id, set())
            fresh = set(writes) - previous
            previous.update(writes)
            window = windows.get(handoff.run_id, 1)
            latest = {e.payload["result"]["resource"]["resource_id"]: e for e in writes.values()}
            resources = FilesystemResource([attempt.workspace])
            try:
                current = all(
                    resources.read(path).revision
                    == ResourceRevision(**event.payload["result"]["resource"])
                    for path, event in latest.items()
                )
            except (ValueError, OSError):
                current = False
            explicit = attempt.record.reports.get("coder", {}) if attempt.record else {}
            explicit = (
                explicit
                if explicit.get("status") in {"blocked", "needs_context"}
                and explicit.get("reason") not in {"worker_turn_limit", "worker_no_progress"}
                else {}
            )
            safe = (
                fresh
                and current
                and window < coder_work_windows()
                and not attempt.checking
                and not explicit
                and not any(
                    e.type
                    in {"action.failed", "action.denied", "budget.exceeded", "workflow.blocked"}
                    for e in report.events
                )
            )
            if not safe:
                task.metadata["worker_handoff"] = handoff.to_dict()
                if attempt.record:
                    attempt.record.reports["coder"] = explicit or {
                        "status": "needs_context" if fresh and current else "blocked",
                        "reason": "worker_turn_limit"
                        if fresh and current
                        else "worker_no_progress",
                        "summary": "Coder yielded unfinished work; applied edits remain unverified.",
                        "applied_paths": sorted(latest),
                        "verified": False,
                    }
                await emit(
                    RunEvent(
                        type="worker.handoff",
                        run_id=handoff.run_id,
                        task_id=task.id,
                        agent_name="coder",
                        summary="Coder returned unfinished checkpointed work.",
                        payload={"handoff": handoff.to_dict()},
                    )
                )
                return task
            windows[handoff.run_id] = window + 1
            await emit(
                RunEvent(
                    type="worker.continuing",
                    run_id=handoff.run_id,
                    task_id=task.id,
                    agent_name="coder",
                    summary=f"Coder continuing saved work (slice {window + 1}/{coder_work_windows()})",
                    payload={
                        "handoff": handoff.to_dict(),
                        "window": window + 1,
                        "max_windows": coder_work_windows(),
                    },
                )
            )
        elif pending.get("kind") == "approval":
            request = ApprovalRequest.from_dict(pending["request"])
            context = attempt.authorization.context.copy()
            context.run_id = request.run_id
            decision = await broker(request, context)
            response = {
                "request_id": pending["request_id"],
                "fingerprint": pending["fingerprint"],
                "approved": decision.approved,
            }
        elif pending.get("kind") == "input":
            data = pending["request"]
            request = UserInputRequest(
                **{key: value for key, value in data.items() if key != "max_answer_chars"}
            )
            answer = await user_input_handler(request) if user_input_handler else None
            response = {
                "request_id": pending["request_id"],
                "fingerprint": pending["fingerprint"],
                "answer": answer,
            }
        else:
            return task
        # A run ID is not authorization: this is the configured owner and its
        # current native interruption, after application policy and exact input.
        # Keep the SDK's streaming acquisition path after approvals, questions
        # and yields too; resume_task otherwise uses non-streaming inference.
        with durable_streaming_scope():
            task = await parent.resume_task(root_run_id, **response)
        recorded = task.metadata.setdefault("run_events", [])
        known = {event["event_id"] for event in recorded}
        recorded.extend(event for event in application_events if event["event_id"] not in known)
    return task
