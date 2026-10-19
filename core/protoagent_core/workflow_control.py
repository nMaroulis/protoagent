"""Return unfinished editing to the bounded native workflow."""

from __future__ import annotations

from contextvars import ContextVar

from protolink import Agent, AgentHooks, Part, RunContext, RunEvent, RunReport, Task
from protolink.core.execution import publish_runtime_event

from .runtime_policy import AttemptState


class PartialHandoff(Exception):
    """Return a newly exhausted Coder assignment directly to the workflow."""


class WorkflowArchitectAgent(Agent):
    """Yield unfinished edits; verification never enters the model loop."""

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
            *args, hooks=(*hooks, AgentHooks(before_model=self._yield_partial)), **kwargs
        )

    def _yield_partial(self, request):
        task = self._checking_task.get()
        if task and RunContext.from_task(task).run_id == request.context.run_id:
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
        except PartialHandoff:
            if task is None or self.work_attempt is None:
                raise RuntimeError("Workflow handoff requires its native task and attempt")
            token = kwargs.get("cancellation_token") or self.get_cancellation_token(task.id)
            if token:
                token.raise_if_cancelled()
            context = RunContext.from_task(task)
            event = self._new_partial(task)
            if context.canceled or not event:
                raise RuntimeError("Stopped execution cannot be handed to another workflow phase")
            await publish_runtime_event(
                RunEvent(
                    type="workflow.work_yielded",
                    run_id=context.run_id,
                    task_id=task.id,
                    agent_name=self.card.name,
                    summary="Coder returned partial work; continuing through the bounded workflow",
                    payload={
                        "handoff_event_id": event.event_id,
                        "attempt": self.work_attempt.attempt,
                    },
                )
            )
            return Part(
                "infer_output",
                "Coder returned partial work. Returning control to the bounded workflow.",
            )
        finally:
            self._checking_task.reset(binding)
            self._prior_handoffs.reset(prior)
