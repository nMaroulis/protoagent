"""ProtoAgent's edit/check/repair workflow, bounded by the native ProtoLink Graph."""

from __future__ import annotations

import asyncio
import json
import logging

from protolink import Graph, Message, RunContext, RunEvent, RunReport, Task, TaskState
from protolink.core.execution import publish_runtime_event
from protolink.flows import Flow

from .verification import validate_completion


class CodingWorkflow:
    """Keep coding acceptance and repair instructions outside the execution engine."""

    def __init__(self, *, group, contract, attempt, broker, observe, redaction, prompt):
        self.group, self.contract, self.attempt = group, contract, attempt
        self.broker, self.observe, self.redaction = broker, observe, redaction
        self.prompt = prompt
        self.task = None
        self.acceptance = None
        self.answer = ""
        self.uncertain = False
        self.continuations = 0

    async def execute(self, context):
        """Bound initial work, partial continuations and qualifying check repairs."""
        workflow = self

        class Attempt(Flow):
            def __init__(self, *, continuation=False):
                super().__init__()
                self.continuation = continuation

            async def execute(self, task):
                if self.continuation:
                    workflow.continuations += 1
                    await publish_runtime_event(
                        RunEvent(
                            type="workflow.continuing",
                            run_id=RunContext.from_task(task).run_id,
                            task_id=task.id,
                            agent_name="architect",
                            summary=f"Continuing unfinished edits ({workflow.continuations}/2)",
                            payload={
                                "continuation": workflow.continuations,
                                "attempt": workflow.attempt.attempt,
                            },
                        )
                    )
                else:
                    workflow.attempt.begin()
                instruction = workflow.prompt
                if workflow.attempt.record and (
                    workflow.contract.requires_write
                    or workflow.contract.task_kind == "workspace-verification"
                ):
                    packet = workflow.attempt.record.snapshot()
                    packet.pop("available_checks", None)
                    instruction += "\n\nRuntime task packet:\n" + json.dumps(packet)
                if self.continuation:
                    instruction += (
                        "\n\nContinue the current edit phase after Coder returned applied partial work. "
                        "The previous final answer did not finish the task. The plan and checks remain frozen; "
                        "baseline has already been measured. Read current files with an actual tool call, "
                        "identify one concrete unfinished part and delegate only that part to Coder. "
                        "Then run required final checks after the handoff is resolved. "
                        "The user already requested this task: do not ask whether to continue "
                        "solely because a worker work window ended. "
                        "Do not repeat applied edits or merely announce a future tool call. "
                        "Tool JSON embedded in prose is not an executed action. "
                        "This is a bounded continuation within the same shared run budget, not a repair "
                        "or replay of a failed action."
                    )
                elif workflow.attempt.attempt > 1:
                    assert workflow.acceptance is not None
                    instruction += (
                        "\n\nThis is a new bounded repair attempt after a completed failing check. "
                        "Inspect the failure, apply one focused repair, then rerun the affected checks. "
                        "Do not replay interrupted or denied actions. Return after checking.\n"
                        "Do not install dependencies or alter application code to hide an environment failure. "
                        "Missing runners/dependencies end with unavailable verification rather than a repair.\n"
                        + json.dumps(workflow.acceptance.verification["latest"], ensure_ascii=True)[
                            :12000
                        ]
                    )
                task.add_message(Message.infer(prompt=instruction))
                handle = workflow.group.run("architect", task, redaction_policy=workflow.redaction)
                try:
                    await workflow.observe(handle)
                    result = await handle.result()
                except asyncio.CancelledError:
                    await handle.cancel("Enclosing workflow canceled")
                    await handle.result()
                    raise
                finally:
                    # Graph nodes exchange Tasks. Carry the handle's complete
                    # native report, including model stream/metrics events, into
                    # that task; native Graph merging preserves it across nodes.
                    task.metadata["run_events"] = [
                        event.to_dict() for event in handle.report.events
                    ]
                if result.task is not None:
                    result.task.metadata["run_events"] = task.metadata["run_events"]
                if result.status == "uncertain" or result.task is None:
                    workflow.uncertain = True
                    raise RuntimeError(
                        "Architect response is uncertain; execution will not be replayed"
                    )
                if result.error:
                    raise RuntimeError(f"Native run failed: {result.error}")
                from .runtime import _content_to_text

                workflow.answer = _content_to_text(result.output)
                return result.task

        class Accept(Flow):
            async def execute(self, task):
                # ProtoLink propagates worker receipts into the parent's native task.
                report = RunReport.from_task(task)
                workflow.acceptance = await validate_completion(
                    workflow.contract, task, report, workflow.attempt, workflow.broker
                )
                task.metadata["completion_validation"] = workflow.acceptance.completion
                task.metadata["verification"] = workflow.acceptance.verification
                task.metadata["workflow_continuations"] = workflow.continuations
                task.begin()
                task.update_state(TaskState.COMPLETED)
                return task

        graph = Graph(
            max_iterations=11,
            max_node_visits={"initial": 1, "repair": 2, "continue": 2, "accept": 5},
        )
        logging.getLogger("protolink.flows.Graph").setLevel(logging.ERROR)
        graph.add_node("initial", Attempt()).add_node("repair", Attempt()).add_node(
            "accept", Accept()
        ).add_node("continue", Attempt(continuation=True))
        graph.add_edge("initial", "accept").add_edge("repair", "accept").add_edge(
            "continue", "accept"
        )
        graph.add_conditional_edge(
            "accept",
            lambda task: (
                "repair"
                if self.acceptance and self.acceptance.repairable and self.attempt.attempt < 3
                else "continue"
                if self.acceptance and self.acceptance.continuable and self.continuations < 2
                else "done"
            ),
            {"repair": "repair", "continue": "continue", "done": graph.finish_point},
        ).set_entry_point("initial")
        self.task = Task.create_infer(prompt=self.prompt)
        context.child().attach_to_task(self.task)
        result = await graph.execute(self.task)
        result.raise_for_status()
        return self.answer
