"""Edit first, then run one software-controlled verification pass."""

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

    def __init__(
        self,
        *,
        group,
        contract,
        attempt,
        broker,
        observe,
        redaction,
        prompt,
        user_input_handler=None,
        event_handler=None,
    ):
        self.group, self.contract, self.attempt = group, contract, attempt
        self.broker, self.observe, self.redaction = broker, observe, redaction
        self.prompt = prompt
        self.task = None
        self.acceptance = None
        self.answer = ""
        self.native_status = None
        self.uncertain = False
        self.continuations = 0
        self.user_input_handler = user_input_handler
        self.event_handler = event_handler

    async def execute(self, context):
        """Bound partial editing; checks never add another model conversation."""
        workflow = self

        class Attempt(Flow):
            def __init__(self, *, continuation=False):
                super().__init__()
                self.continuation = continuation

            async def execute(self, task):
                if workflow.contract.task_kind == "workspace-verification":
                    workflow.attempt.begin()
                    workflow.answer = "Repository checks finished."
                    workflow.native_status = "completed"
                    task.begin()
                    task.update_state(TaskState.COMPLETED)
                    return task
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
                    for key in (
                        "available_checks",
                        "available_check_ids",
                        "baseline_check_ids",
                        "bootstrap_checks",
                    ):
                        packet.pop(key, None)
                    instruction += "\n\nRuntime task packet:\n" + json.dumps(packet)
                if self.continuation:
                    instruction += (
                        "\n\nContinue the current edit phase after Coder returned applied partial work. "
                        "The previous final answer did not finish the task. The plan and checks remain frozen; "
                        "checks run after editing. Read current files with an actual tool call, "
                        "identify one concrete unfinished part and delegate only that part to Coder. "
                        "Finish the edits; the harness runs checks after the handoff is resolved. "
                        "The user already requested this task: do not ask whether to continue "
                        "solely because a worker work window ended. "
                        "Do not repeat applied edits or merely announce a future tool call. "
                        "Tool JSON embedded in prose is not an executed action. "
                        "This is a bounded continuation within the same shared run budget, not a repair "
                        "or replay of a failed action."
                    )
                if workflow.group.agents["architect"].durability is not None:
                    # Graph nodes can exchange new Task identities. A new
                    # assignment/repair gets a fresh root checkpoint; only an
                    # explicit resume reuses a saved execution cursor.
                    RunContext.from_task(task).child().attach_to_task(task)
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
                    from .worker_execution import continue_workers

                    continued = await continue_workers(
                        workflow.group.agents["architect"],
                        result.task,
                        attempt=workflow.attempt,
                        broker=workflow.broker,
                        user_input_handler=workflow.user_input_handler,
                        event_handler=workflow.event_handler,
                    )
                    # Keep committed resumed receipts on the Graph's input too:
                    # its failure path cannot return the resumed Task object.
                    task.metadata["run_events"] = continued.metadata.get("run_events", [])
                    if continued is not result.task:
                        from protolink import RunResult

                        result = RunResult(
                            continued.state.value,
                            continued,
                            continued.get_output(),
                            RunReport.from_task(continued),
                            {"code": "task_failed", "message": continued.metadata.get("error")}
                            if continued.state.value == "failed"
                            else None,
                        )
                if result.status == "uncertain" or result.task is None:
                    workflow.uncertain = True
                    raise RuntimeError(
                        "Architect response is uncertain; execution will not be replayed"
                    )
                if result.error:
                    raise RuntimeError(f"Native run failed: {result.error}")
                workflow.native_status = result.status
                from .runtime import _content_to_text

                workflow.answer = _content_to_text(result.output)
                return result.task

        class Check(Flow):
            async def execute(self, task):
                record = workflow.attempt.record
                if (
                    not record
                    or not record.verification_enabled
                    or workflow.native_status != "completed"
                    or workflow.attempt.denied
                    or workflow.attempt.has_uncertain_changes()
                    or RunContext.from_task(task).canceled
                    or any(worker["status"] != "done" for worker in record.reports.values())
                ):
                    return task
                report = RunReport.from_task(task)
                if any(
                    event.type
                    in {"action.failed", "action.denied", "budget.exceeded", "workflow.blocked"}
                    for event in report.events
                ):
                    return task
                applied = any(
                    event.type == "action.completed"
                    and isinstance(event.payload.get("result"), dict)
                    and event.payload["result"].get("state") == "applied"
                    for event in report.events
                )
                if workflow.contract.task_kind != "workspace-verification" and (
                    not applied or workflow.contract.allows_unverified_docs
                ):
                    return task
                verifier = workflow.group.agents.get("verifier")
                if verifier is None:
                    return task
                from .editing import check_tool

                # The check tool is absent from model rosters. The application
                # selects captured check IDs and uses native AgentGroup execution.
                if "run_check" not in verifier.tools:
                    verifier.add_tool(check_tool(verifier.tools["execute_command"], record))
                ready = set(record.baseline_check_ids())
                for check_id in record.selected:
                    if check_id not in ready:
                        continue
                    check = Task.create_tool_call(
                        "run_check", {"check_id": check_id, "phase": "verify"}
                    )
                    RunContext.from_task(task).child().attach_to_task(check)
                    handle = workflow.group.run(
                        verifier, check, redaction_policy=workflow.redaction
                    )
                    try:
                        await workflow.observe(handle)
                        result = await handle.result()
                    except asyncio.CancelledError:
                        await handle.cancel("Enclosing workflow canceled")
                        await handle.result()
                        raise
                    finally:
                        records = task.metadata.setdefault("run_events", [])
                        known = {event["event_id"] for event in records}
                        records.extend(
                            event.to_dict()
                            for event in handle.report.events
                            if event.event_id not in known
                        )
                    if result.error:
                        if any(event.type == "action.denied" for event in handle.report.events):
                            # A declined check stops the pass. Acceptance reports
                            # the native denial without failing the edit controller.
                            return task
                        raise RuntimeError(f"Verification could not execute: {result.error}")
                    if result.status != "completed" or workflow.attempt.denied:
                        break
                return task

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
            max_iterations=9,
            max_node_visits={"initial": 1, "continue": 2, "check": 3, "accept": 3},
        )
        logging.getLogger("protolink.flows.Graph").setLevel(logging.ERROR)
        graph.add_node("initial", Attempt()).add_node("check", Check()).add_node(
            "accept", Accept()
        ).add_node("continue", Attempt(continuation=True))
        graph.add_edge("initial", "check").add_edge("continue", "check").add_edge("check", "accept")
        graph.add_conditional_edge(
            "accept",
            lambda task: (
                "continue"
                if self.acceptance and self.acceptance.continuable and self.continuations < 2
                else "done"
            ),
            {"continue": "continue", "done": graph.finish_point},
        ).set_entry_point("initial")
        self.task = Task.create_infer(prompt=self.prompt)
        context.child().attach_to_task(self.task)
        from .worker_execution import checkpointed_workers

        with checkpointed_workers(self.group.agents["architect"], context):
            context.child().attach_to_task(self.task)
            result = await graph.execute(self.task)
        result.raise_for_status()
        return self.answer
