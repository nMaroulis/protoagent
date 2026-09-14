"""ProtoAgent's edit/check/repair workflow, bounded by the native ProtoLink Graph."""

from __future__ import annotations

import asyncio
import json
import logging

from protolink import Graph, Message, RunReport, Task, TaskState
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

    async def execute(self, context):
        """Run one initial attempt and at most two new, explicitly bounded repairs."""
        workflow = self

        class Attempt(Flow):
            async def execute(self, task):
                workflow.attempt.begin()
                instruction = workflow.prompt
                if workflow.attempt.attempt > 1:
                    assert workflow.acceptance is not None
                    instruction += (
                        "\n\nThis is a new bounded repair attempt after a completed failing check. "
                        "Inspect the failure, apply one focused repair, then rerun the affected checks. "
                        "Do not replay interrupted or denied actions. Return after checking.\n"
                        + json.dumps(workflow.acceptance.verification, ensure_ascii=True)
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
                task.begin()
                task.update_state(TaskState.COMPLETED)
                return task

        graph = Graph(max_iterations=7, max_node_visits={"initial": 1, "repair": 2, "accept": 3})
        logging.getLogger("protolink.flows.Graph").setLevel(logging.ERROR)
        graph.add_node("initial", Attempt()).add_node("repair", Attempt()).add_node(
            "accept", Accept()
        )
        graph.add_edge("initial", "accept").add_edge("repair", "accept")
        graph.add_conditional_edge(
            "accept",
            lambda task: "repair" if self.acceptance and self.acceptance.repairable else "done",
            {"repair": "repair", "done": graph.finish_point},
        ).set_entry_point("initial")
        self.task = Task.create_infer(prompt=self.prompt)
        context.child().attach_to_task(self.task)
        result = await graph.execute(self.task)
        result.raise_for_status()
        return self.answer
