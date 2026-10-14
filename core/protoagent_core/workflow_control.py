"""Return completed failing checks to the native bounded repair graph."""

from __future__ import annotations

from contextvars import ContextVar

from protolink import Agent, AgentHooks, Part, RunContext, RunEvent, RunReport, Task
from protolink.core.execution import publish_runtime_event

from .runtime_policy import AttemptState


class CheckedHandoff(Exception):
    """Application control signal, raised before another model request."""


class PartialHandoff(Exception):
    """Return a newly exhausted Coder assignment directly to the workflow."""


class WorkflowArchitectAgent(Agent):
    """Keep failed verification separate from the next authorized edit phase.

    Completed failing checks and newly exhausted partial assignments yield.
    Provider/action errors, cancellation, uncertainty and denials retain their
    native stop behavior. Prior handoffs do not interrupt a new Graph node.
    """

    def __init__(self, *args, work_attempt: AttemptState | None = None, **kwargs):
        self.work_attempt = work_attempt
        self._checking_task: ContextVar[Task | None] = ContextVar(
            "protoagent_checking_task", default=None
        )
        self._prior_handoffs: ContextVar[frozenset[str]] = ContextVar(
            "protoagent_prior_handoffs", default=frozenset()
        )
        hooks = tuple(kwargs.pop("hooks", ()) or ())
        super().__init__(
            *args, hooks=(*hooks, AgentHooks(before_model=self._yield_failed_check)), **kwargs
        )

    def _failed_check(self, task):
        attempt = self.work_attempt
        if (
            not task
            or not attempt
            or not attempt.checking
            or attempt.denied
            or attempt.has_uncertain_changes()
        ):
            return None
        report = RunReport.from_task(task)
        if any(
            event.type in {"action.failed", "action.denied", "budget.exceeded", "workflow.blocked"}
            for event in report.events
        ):
            return None
        for event in reversed(report.events):
            if (
                event.type != "action.completed"
                or attempt.command_phases.get(event.action_id) != "verify"
                or attempt.command_attempts.get(event.action_id) != attempt.attempt
                or not attempt.command_checks.get(event.action_id)
            ):
                continue
            result = event.payload.get("result")
            if (
                isinstance(result, dict)
                and isinstance(result.get("exit_code"), int)
                and result["exit_code"] != 0
                and not any(
                    result.get(flag) for flag in ("timed_out", "canceled", "budget_exceeded")
                )
            ):
                return event
        return None

    def _yield_failed_check(self, request):
        task = self._checking_task.get()
        if task and RunContext.from_task(task).run_id == request.context.run_id:
            if self._failed_check(task):
                request.tools.clear()
                raise CheckedHandoff()
            if self._new_partial(task):
                request.tools.clear()
                raise PartialHandoff()

    def _new_partial(self, task):
        attempt = self.work_attempt
        if not attempt or attempt.checking or attempt.denied or attempt.has_uncertain_changes():
            return None
        record = attempt.record
        if not record or record.reports.get("coder", {}).get("reason") != "worker_turn_limit":
            return None
        report = RunReport.from_task(task)
        if any(
            event.type in {"action.failed", "action.denied", "budget.exceeded", "workflow.blocked"}
            for event in report.events
        ):
            return None
        return next(
            (
                event
                for event in reversed(report.events)
                if event.type == "worker.handoff"
                and event.event_id not in self._prior_handoffs.get()
                and event.agent_name == "coder"
                and event.payload.get("handoff", {}).get("reason") == "worker_turn_limit"
            ),
            None,
        )

    async def call_llm(self, infer_part, task: Task | None = None, **kwargs):
        binding = self._checking_task.set(task)
        prior = self._prior_handoffs.set(
            frozenset(
                event.event_id
                for event in RunReport.from_task(task).events
                if event.type == "worker.handoff"
            )
            if task
            else frozenset()
        )
        try:
            return await super().call_llm(infer_part, task=task, **kwargs)
        except (CheckedHandoff, PartialHandoff) as handoff:
            if task is None or self.work_attempt is None:
                raise RuntimeError("Workflow handoff requires its native task and attempt")
            token = kwargs.get("cancellation_token") or self.get_cancellation_token(task.id)
            if token:
                token.raise_if_cancelled()
            context = RunContext.from_task(task)
            partial = isinstance(handoff, PartialHandoff)
            event = self._new_partial(task) if partial else self._failed_check(task)
            if context.canceled or not event:
                raise RuntimeError("Stopped execution cannot be handed to another workflow phase")
            from .verification import verification_setup_issue

            issue = (
                None
                if partial
                else verification_setup_issue(
                    event.payload["result"],
                    event.payload["action"]["payload"]["arguments"],
                    self.work_attempt.workspace,
                )
            )
            await publish_runtime_event(
                RunEvent(
                    type="workflow.work_yielded"
                    if partial
                    else "verification.unavailable"
                    if issue
                    else "workflow.repair.requested",
                    run_id=context.run_id,
                    task_id=task.id,
                    agent_name=self.card.name,
                    summary="Coder returned partial work; continuing through the bounded workflow"
                    if partial
                    else issue["message"]
                    if issue
                    else "Check failed; returning control to the bounded repair workflow",
                    payload={
                        "handoff_event_id": event.event_id,
                        "check_action_id": event.action_id if not partial else None,
                        "attempt": self.work_attempt.attempt,
                        **({"setup_issue": issue} if issue else {}),
                    },
                )
            )
            return Part(
                "infer_output",
                "Coder returned partial work. Returning control to the bounded workflow."
                if partial
                else "Verification unavailable: " + issue["message"]
                if issue
                else "A required check failed. Returning the measured result to the bounded repair workflow.",
            )
        finally:
            self._checking_task.reset(binding)
            self._prior_handoffs.reset(prior)
