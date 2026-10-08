"""Offline engine-contract evaluation with real application reads and child runs."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from protolink import (
    Agent,
    CapabilityPolicy,
    EvaluationCase,
    EvaluationScore,
    SubagentLimits,
    evaluate,
    exact_match,
)
from protolink.llms.factory import create_llm

from .agents.explorer import create_explorer_agent
from .request_budget import configure_agent_context
from .task_record import TaskRecord


def run_harness_eval(*, repetitions: int = 2) -> dict:
    """Exercise actual native execution with scripted actions, never a live model.

    Each sample receives fresh application task state, a child and a model
    conversation. The shared disposable workspace contains read-only fixtures.
    Scores measure integration contracts, not reasoning or coding quality.
    """
    with tempfile.TemporaryDirectory(prefix="protoagent-harness-eval-") as directory:
        root = Path(directory).resolve()
        (root / "fixture.py").write_text("value = 42\n")

        def factory():
            record = TaskRecord.create(str(root), "Explain fixture.py")
            child = create_explorer_agent(
                provider="mock",
                model="offline",
                workspace=str(root),
                transport=None,
                prompt_profile="small",
                record=record,
            )

            def scripted(history, prompt):
                observations = [
                    json.loads(message["content"])
                    for message in history.to_list()
                    if message["content"].startswith('{"type": "agent_result"')
                ]
                if observations:
                    result = observations[-1]["result"]
                    result = result.get("result", result) if isinstance(result, dict) else result
                    if "objective" in result:
                        content = result["objective"]
                    else:
                        content = "value = 42" if "value = 42" in str(result) else "missing source"
                    return {"type": "final", "content": content}
                query = next(
                    message["content"]
                    for message in reversed(history.to_list())
                    if message["role"] == "user"
                )
                tool = "task_status" if "task-state" in query else "read_file"
                return {
                    "type": "agent_call",
                    "agent": "explorer",
                    "action": "tool_call",
                    "tool": tool,
                    "args": {} if tool == "task_status" else {"path": str(root / "fixture.py")},
                }

            parent = Agent(
                name="architect",
                llm=create_llm("mock", response_callback=scripted),
                system_prompt="Read the configured fixture through Explorer; return its measured value.",
                subagents=[child],
                subagent_limits=SubagentLimits(max_children=2, max_concurrency=1, max_depth=1),
                policy=CapabilityPolicy(
                    {"agent.delegate": "allow", "workspace.read": "allow", "task.manage": "allow"},
                    default_effect="deny",
                ),
                verbosity=0,
            )
            configure_agent_context(
                parent, record, fallback_window=8192, compact_protocol=True, cards=[child.card]
            )
            return parent

        def linked_receipt(sample):
            tool = sample.case.metadata["tool"]
            found = (
                any(
                    event.type == "action.completed"
                    and isinstance(event.payload.get("action"), dict)
                    and event.payload.get("action", {}).get("name") == tool
                    and event.parent_action_id
                    and event.delegation_id
                    for event in sample.report.events
                    if sample.report is not None
                )
                if sample.report is not None
                else False
            )
            return EvaluationScore("linked_child_receipt", float(bool(found)))

        cases = [
            EvaluationCase("read-source", "read-source", "value = 42", {"tool": "read_file"}),
            EvaluationCase(
                "task-state", "task-state", "Explain fixture.py", {"tool": "task_status"}
            ),
        ]
        report = asyncio.run(
            evaluate(factory, cases, checks=[exact_match, linked_receipt], repetitions=repetitions)
        )
        return {
            "engine": "ProtoLink evaluate",
            "mode": "offline",
            **report.to_dict(),
            "notes": [
                "Scripted MockLLM actions; no provider requests, approvals, workspace writes or user configuration changes.",
                "Checks application source reads, task-state handoffs and linked native child receipts.",
                "This measures runtime integration, not real-model quality; use eval coding --live for coding outcomes.",
            ],
        }
