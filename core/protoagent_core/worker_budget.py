"""Stop a file worker's work slice and hand native progress back to its controller."""

from __future__ import annotations

import json
import os
from contextvars import ContextVar

from protolink import Agent, AgentHooks, Part, ResourceRevision, RunContext, RunEvent, RunReport
from protolink.core.execution import publish_runtime_event
from protolink.llms.base import MAX_INFER_STEPS
from protolink.tools.builtins.filesystem import FilesystemResource

_work_window = ContextVar("protoagent_coder_work_window", default=1)


def coder_work_windows() -> int:
    """A lower user-selected bound cannot enlarge the application ceiling."""
    try:
        return min(3, max(1, int(os.getenv("PROTOAGENT_CODER_MAX_WINDOWS", "3"))))
    except ValueError:
        return 3


def applied_writes(report, run_id):
    """Assignment-local native receipts, never inherited or displayed proposals."""
    return {
        event.action_id: event
        for event in report.events
        if event.run_id == run_id
        and event.type == "action.completed"
        and isinstance(event.payload.get("action"), dict)
        and event.payload["action"].get("name") in {"create_file", "replace_file"}
        and isinstance(event.payload.get("result"), dict)
        and event.payload["result"].get("state") == "applied"
        and isinstance(event.payload["result"].get("resource"), dict)
    }


class CoderHandoff(Exception):
    """Application control signal raised before another provider/tool request."""

    def __init__(self, context, turns):
        super().__init__("Coder work slice ended")
        self.context, self.turns = context, turns


class BoundedCoderAgent(Agent):
    """Keep native inference/execution; consume only our own pre-model stop signal.

    This public Agent extension admits at most three native work windows when
    fresh writes justify continuation. It catches only its own pre-model signal,
    never native action errors or a provider failure, and never replays receipts.
    Streaming, owned children and transport handlers all use call_llm.
    """

    def __init__(self, *args, work_record=None, work_attempt=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.work_record, self.work_attempt = work_record, work_attempt

    async def call_llm(self, infer_part, task=None, **kwargs):
        run_id = RunContext.from_task(task).run_id if task is not None else ""
        original_assignment = self._assignment(infer_part)
        maximum = coder_work_windows()
        for window in range(1, maximum + 1):
            prior = set(applied_writes(RunReport.from_task(task), run_id)) if task else set()
            binding = _work_window.set(window)
            try:
                return await super().call_llm(infer_part, task=task, **kwargs)
            except CoderHandoff as handoff:
                if task is None:
                    raise RuntimeError(
                        "Coder work continuation requires its native task"
                    ) from handoff
                report = self._safe_handoff(task, handoff, kwargs)
                writes = applied_writes(report, run_id)
                fresh = {key: event for key, event in writes.items() if key not in prior}
                latest = {
                    event.payload["result"]["resource"]["resource_id"]: event
                    for event in writes.values()
                }
                explicit = self.work_record.reports.get("coder", {}) if self.work_record else {}
                if (
                    fresh
                    and window < maximum
                    and self.work_attempt
                    and not self.work_attempt.checking
                    and not (
                        explicit.get("status") in {"blocked", "needs_context"}
                        and explicit.get("reason")
                        not in {"worker_turn_limit", "worker_no_progress"}
                    )
                    and self._current_writes(latest)
                ):
                    await publish_runtime_event(
                        RunEvent(
                            type="worker.continuing",
                            run_id=run_id,
                            task_id=task.id,
                            agent_name=self.card.name,
                            summary=f"Coder continuing applied work (window {window + 1}/{maximum})",
                            payload={
                                "window": window + 1,
                                "max_windows": maximum,
                                "new_write_actions": sorted(fresh),
                                "applied_paths": sorted(latest),
                            },
                        )
                    )
                    # This is new work from the committed state, not a replay of
                    # the old request or an interrupted provider/tool call.
                    infer_part = Part(
                        "infer",
                        {
                            "prompt": (
                                "Continue the SAME assigned task from the current files. "
                                "The previous work window ended before another model request. "
                                "Keep the original scope and criteria; finish only concrete remaining edits. "
                                "Do not redo applied edits, reread known source unnecessarily, expand scope, "
                                "or run verification. Use the last successful source_revision for known text. "
                                "Return a concise final outcome as soon as the assignment is finished.\n"
                                + json.dumps(
                                    {
                                        "window": window + 1,
                                        "max_windows": maximum,
                                        "applied_paths": sorted(latest),
                                        "original_assignment": original_assignment,
                                    },
                                    ensure_ascii=True,
                                )
                            )
                        },
                    )
                    continue
                return await self._return_handoff(task, handoff, writes, bool(fresh), window)
            finally:
                _work_window.reset(binding)
        raise AssertionError("Coder work-window bound must return an outcome")

    @staticmethod
    def _assignment(part):
        content = part.content
        return (
            content.get("original_assignment", content.get("prompt", ""))
            if isinstance(content, dict)
            else str(content)
        )

    def _current_writes(self, latest):
        if self.work_attempt is None:
            return False
        resources = FilesystemResource([self.work_attempt.workspace])
        try:
            return all(
                resources.read(path).revision
                == ResourceRevision(**event.payload["result"]["resource"])
                for path, event in latest.items()
            )
        except (OSError, ValueError):
            return False

    def _safe_handoff(self, task, handoff, kwargs):
        if task is None or RunContext.from_task(task).run_id != handoff.context.run_id:
            raise RuntimeError("Coder handoff requires its native task context") from handoff
        token = kwargs.get("cancellation_token") or self.get_cancellation_token(task.id)
        if token:
            token.raise_if_cancelled()
        if handoff.context.canceled:
            raise RuntimeError("Coder was canceled before its handoff") from handoff
        if self.work_attempt and (
            self.work_attempt.denied or self.work_attempt.has_uncertain_changes()
        ):
            raise RuntimeError("Coder cannot hand off denied or uncertain effects") from handoff
        report = RunReport.from_task(task)
        if any(
            event.type in {"action.failed", "action.denied", "budget.exceeded", "workflow.blocked"}
            for event in report.events
        ):
            raise RuntimeError("Coder cannot hand off a failed native action") from handoff
        return report

    async def _return_handoff(self, task, handoff, writes, progressed, window):
        paths = sorted(
            {event.payload["result"]["resource"]["resource_id"] for event in writes.values()}
        )
        explicit = self.work_record.reports.get("coder", {}) if self.work_record else {}
        explicit = (
            explicit
            if explicit.get("status") in {"blocked", "needs_context"}
            and explicit.get("reason") not in {"worker_turn_limit", "worker_no_progress"}
            else {}
        )
        reason = (
            "worker_report"
            if explicit
            else "worker_turn_limit"
            if progressed
            else "worker_no_progress"
        )
        status = explicit["status"] if explicit else "needs_context" if progressed else "blocked"
        turns = (window - 1) * (MAX_INFER_STEPS - 1) + handoff.turns
        summary = (
            explicit["summary"]
            if explicit
            else f"Coder stopped after {turns} model turns ({window} work windows). "
            + (
                "Applied edits are partial; the assignment still needs review."
                if progressed
                else "No new file change was applied in the last work window; do not repeat it unchanged."
            )
        )
        result = {
            **explicit,
            "status": status,
            "reason": reason,
            "summary": summary,
            "applied_paths": paths,
            "verified": False,
            "work_windows": window,
            "model_turns": turns,
            "next_step": (
                "Resolve the worker's explicit missing context or blocker before further work."
                if explicit
                else "Inspect current files, identify concrete unfinished edits and delegate only "
                "that smaller assignment before final verification. Never replay applied edits."
                if progressed
                else "Stop and explain the missing progress. Gather specific missing context or "
                "clarify the assignment before trying again; do not loop on the same task."
            ),
        }
        if self.work_record:
            worker = explicit or self.work_record.report("coder", status, summary, [])
            worker.update(
                reason=reason, applied_paths=paths, work_windows=window, model_turns=turns
            )
        await publish_runtime_event(
            RunEvent(
                type="worker.handoff",
                run_id=handoff.context.run_id,
                task_id=task.id,
                agent_name=self.card.name,
                step=turns,
                summary=summary,
                payload={"handoff": result},
            )
        )
        return Part("infer_output", result)


def reserve_coder_turn(request) -> str:
    """Stop before the last provider call; early reminders are only an affordance."""
    if request.step >= MAX_INFER_STEPS:
        request.tools.clear()
        raise CoderHandoff(request.context, request.step - 1)
    if request.step >= MAX_INFER_STEPS - 2:
        return (
            f"Coder work window {_work_window.get()}/{coder_work_windows()}, turn {request.step}/{MAX_INFER_STEPS}. Work in short exact edits. "
            "Use the successful edit's source_revision for the next edit of known source; "
            "reread only for missing source or a source rejection. "
            "Return immediately when your assignment is complete."
        )
    return ""


def coder_budget_hooks(record=None):
    """Bound each native inference window through public hooks.

    A final handoff is assignment state, not proof of execution or correctness.
    Architect may issue a narrower new assignment within the shared run budget;
    unresolved handoffs prevent application completion even if checks pass.
    """

    def before_model(request):
        if instructions := reserve_coder_turn(request):
            request.history.messages_raw()[0].content += "\n\n" + instructions

    def before_complete(final):
        if record and record.reports.get("coder", {}).get("reason") in {
            "worker_turn_limit",
            "worker_no_progress",
        }:
            # A later focused assignment returned normally. Explicit reports
            # from that assignment replace the old handoff themselves.
            record.report("coder", "done", str(final.content), [])

    return AgentHooks(before_model=before_model, before_complete=before_complete)
