"""Completed failing checks yield before a model can edit the closed phase."""

import asyncio
import json
from unittest.mock import patch

from protolink.llms.factory import create_llm
from runtime_support import NativeRuntimeCase

from protoagent_core.runtime import _run_agent_deck


class WorkflowControlTests(NativeRuntimeCase):
    async def run_workflow(
        self,
        architect,
        coder,
        *,
        answer=None,
        approve_commands=True,
        verifier_enabled=True,
        prompt="Fix sample.py so value is 42",
    ):
        with (
            patch("protoagent_core.agents.coder.create_selected_llm", return_value=coder),
            patch("protoagent_core.agents.architect.create_selected_llm", return_value=architect),
            patch("protoagent_core.agents.explorer.create_selected_llm", return_value=None),
            patch.dict("os.environ", {"PROTOAGENT_AGENT_TRANSPORT": "local"}),
        ):
            running = asyncio.create_task(
                _run_agent_deck(
                    prompt,
                    "mock",
                    "offline",
                    str(self.root),
                    None,
                    self.bridge,
                    {"resolved": "small"},
                    tester_enabled=False,
                    verifier_enabled=verifier_enabled,
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
                                            "approved": approve_commands
                                            or request["action"]["name"] != "execute_command",
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

    async def test_command_denial_keeps_edits_and_stops_verification(self):
        self.prepare_source()
        architect = create_llm(
            "mock",
            sequential_responses=[
                {"type": "tool_call", "tool": "coder", "args": {"task": "Fix sample.py"}},
                {"type": "final", "content": "Updated sample.py."},
            ],
        )
        result = await self.run_workflow(
            architect,
            create_llm("mock", sequential_responses=self.assignment(42)),
            approve_commands=False,
        )
        self.assertEqual(result["status"], "blocked", result["answer"])
        self.assertEqual((self.root / "sample.py").read_text(), "value = 42\n")
        self.assertFalse(result["verification"]["results"])
        self.assertEqual(len(result["approval_requests"]), 2)
        self.assertFalse(result["approval_decisions"][-1]["approved"])
        self.assertEqual(architect._current_seq_idx, 2)
