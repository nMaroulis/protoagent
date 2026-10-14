"""Completed failing checks yield before a model can edit the closed phase."""

import asyncio
import json
from unittest.mock import patch

from protolink.llms.factory import create_llm
from runtime_support import NativeRuntimeCase

from protoagent_core.runtime import _run_agent_deck


class WorkflowControlTests(NativeRuntimeCase):
    async def run_workflow(self, architect, coder, *, answer=None):
        with (
            patch("protoagent_core.agents.coder.create_selected_llm", return_value=coder),
            patch("protoagent_core.agents.architect.create_selected_llm", return_value=architect),
            patch("protoagent_core.agents.explorer.create_selected_llm", return_value=None),
            patch.dict("os.environ", {"PROTOAGENT_AGENT_TRANSPORT": "local"}),
        ):
            running = asyncio.create_task(
                _run_agent_deck(
                    "Fix sample.py so value is 42",
                    "mock",
                    "offline",
                    str(self.root),
                    None,
                    self.bridge,
                    {"resolved": "small"},
                    tester_enabled=False,
                )
            )
            approved = set()
            try:
                async with asyncio.timeout(5):
                    while not running.done():
                        if answer and self.bridge.input_request_path.exists():
                            question = json.loads(self.bridge.input_request_path.read_text())
                            self.bridge.input_response_path.write_text(
                                json.dumps(
                                    {
                                        **{
                                            key: question[key]
                                            for key in (
                                                "request_id",
                                                "run_id",
                                                "task_id",
                                                "action_id",
                                            )
                                        },
                                        "answer": answer,
                                    }
                                )
                            )
                        if self.bridge.request_path.exists():
                            request = json.loads(self.bridge.request_path.read_text())
                            if request["request_id"] not in approved:
                                self.bridge.decision_path.write_text(
                                    json.dumps(
                                        {
                                            "request_id": request["request_id"],
                                            "fingerprint": request["fingerprint"],
                                            "approved": True,
                                        }
                                    )
                                )
                                approved.add(request["request_id"])
                        await asyncio.sleep(0.005)
                    return await running
            finally:
                if not running.done():
                    running.cancel()
                    await asyncio.gather(running, return_exceptions=True)

    def prepare_source(self):
        (self.root / "sample.py").write_text("value = 0\n")
        (self.root / "test_sample.py").write_text(
            "import unittest\n"
            "class Regression(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        self.assertIn('value = 42', open('sample.py').read())\n"
        )

    @staticmethod
    def assignment(value):
        return [
            {
                "type": "tool_call",
                "tool": "replace_file",
                "args": {"path": "sample.py", "content": f"value = {value}\n"},
            },
            {"type": "final", "content": "Applied the assigned edit."},
        ]

    @staticmethod
    def controller_attempt():
        return [
            {"type": "tool_call", "tool": "coder", "args": {"task": "Fix sample.py"}},
            {
                "type": "tool_call",
                "tool": "run_check",
                "args": {"check_id": "python-tests", "phase": "verify"},
            },
        ]

    async def test_failed_check_returns_to_graph_before_a_new_edit(self):
        self.prepare_source()
        seen = []
        replies = iter(
            [
                *self.controller_attempt(),
                *self.controller_attempt(),
                {"type": "final", "content": "Applied and verified."},
            ]
        )

        def reply(history, prompt):
            seen.append(str(history.to_list()))
            return next(replies)

        result = await self.run_workflow(
            create_llm("mock", response_callback=reply),
            create_llm("mock", sequential_responses=[*self.assignment(1), *self.assignment(42)]),
        )
        self.assertEqual(result["status"], "completed", result["answer"])
        self.assertEqual(result["verification"]["attempts"], 2)
        self.assertEqual(result["verification"]["status"], "passed")
        self.assertEqual([item["exit_code"] for item in result["verification"]["results"]], [1, 0])
        self.assertIn("new bounded repair attempt", seen[2])
        self.assertEqual(len(result["approval_requests"]), 4)
        self.assertFalse(
            any(
                event["type"] in {"action.failed", "action.denied"}
                for event in result["run_events"]
            )
        )
        self.assertEqual(
            sum(event["type"] == "workflow.repair.requested" for event in result["run_events"]), 1
        )

    async def test_three_failed_checks_exhaust_repairs_without_another_dispatch(self):
        self.prepare_source()
        architect = create_llm("mock", sequential_responses=self.controller_attempt() * 3)
        coder = create_llm(
            "mock",
            sequential_responses=[
                action for value in (1, 2, 3) for action in self.assignment(value)
            ],
        )
        result = await self.run_workflow(architect, coder)
        self.assertEqual(result["status"], "incomplete", result["answer"])
        self.assertEqual(result["verification"]["attempts"], 3)
        self.assertEqual(len(result["approval_requests"]), 6)
        self.assertEqual(architect._current_seq_idx, 6)
        self.assertEqual((self.root / "sample.py").read_text(), "value = 3\n")
        self.assertFalse(any(event["type"] == "action.denied" for event in result["run_events"]))

    async def test_dependency_failure_reports_unavailable_without_questions_or_repairs(self):
        self.prepare_source()
        (self.root / "test_sample.py").write_text(
            "import unittest\n"
            "class Regression(unittest.TestCase):\n"
            "    def test_dependency(self):\n"
            "        raise ModuleNotFoundError(\"No module named 'fastapi'\")\n"
        )
        architect = create_llm(
            "mock",
            sequential_responses=[
                *self.controller_attempt(),
                {
                    "type": "tool_call",
                    "tool": "ask_user",
                    "args": {
                        "question": "FastAPI is missing from the test environment. Defer verification?"
                    },
                },
                *self.controller_attempt(),
            ],
        )
        result = await self.run_workflow(
            architect,
            create_llm("mock", sequential_responses=self.assignment(42)),
            answer="Don't run tests; I'll verify it myself. Stop here.",
        )
        self.assertEqual(result["status"], "unverified", result["answer"])
        self.assertEqual(result["verification"]["status"], "unavailable")
        self.assertEqual(result["verification"]["attempts"], 1)
        self.assertEqual(result["verification"]["setup_issues"][0]["module"], "fastapi")
        self.assertFalse(
            any(event["type"] == "user_input.requested" for event in result["run_events"])
        )
        self.assertEqual(architect._current_seq_idx, 2)
        self.assertEqual(len(result["approval_requests"]), 2)
        self.assertEqual((self.root / "sample.py").read_text(), "value = 42\n")
