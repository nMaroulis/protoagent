"""Native saved work slices preserve applied effects and the live application."""

from unittest.mock import patch

import test_workflow_control as workflow_fixtures
from protolink.llms.factory import create_llm
from runtime_support import NativeRuntimeCase


class CheckpointedWorkerTests(NativeRuntimeCase):
    async def test_native_resume_cannot_reset_the_shared_model_budget(self):
        source = "value = 0\n" + "# source\n" * 12
        (self.root / "sample.py").write_text(source)
        coder = create_llm(
            "mock",
            sequential_responses=[
                {
                    "type": "tool_call",
                    "tool": "replace_file",
                    "args": {
                        "path": "sample.py",
                        "content": source.replace("value = 0", "value = 1"),
                    },
                },
                *[
                    {
                        "type": "tool_call",
                        "tool": "read_file",
                        "args": {"path": "sample.py", "start_line": line, "end_line": line},
                    }
                    for line in range(2, 12)
                ],
                {"type": "final", "content": "This must not be requested."},
            ],
        )
        architect = create_llm(
            "mock",
            sequential_responses=[
                {
                    "type": "tool_call",
                    "tool": "coder",
                    "args": {"task": "Change sample.py value to 42."},
                },
                {"type": "final", "content": "This must not be requested."},
            ],
        )
        with patch.dict("os.environ", {"PROTOAGENT_RUN_MAX_LLM_CALLS": "10"}):
            result = await workflow_fixtures.WorkflowControlTests.run_workflow(
                self, architect, coder
            )
        self.assertNotEqual(result["status"], "completed", result["answer"])
        self.assertLessEqual(coder._current_seq_idx + architect._current_seq_idx, 10)
        self.assertTrue((self.root / "sample.py").read_text().startswith("value = 1\n"))
        # SDK resume preflight can stop before emitting a new run event. The
        # native error and actual request count still establish the hard stop.
        self.assertIn("max_llm_calls", result["answer"])
        self.assertFalse(result["completion_validation"]["satisfied"])
        self.assertEqual(result["change_review"]["file_count"], 1)
        self.assertFalse(any(e["type"] == "worker.continuing" for e in result["run_events"]))

    async def test_native_slice_continues_same_child_without_repeating_edits_or_approvals(self):
        source = "value = 0\n" + "# source\n" * 12
        (self.root / "sample.py").write_text(source)
        (self.root / "test_sample.py").write_text(
            "import unittest\nimport sample\n"
            "class Regression(unittest.TestCase):\n"
            "    def test_value(self):\n        self.assertEqual(sample.value, 42)\n"
        )

        def write(value):
            return {
                "type": "tool_call",
                "tool": "replace_file",
                "args": {
                    "path": "sample.py",
                    "content": source.replace("value = 0", f"value = {value}"),
                },
            }

        coder = create_llm(
            "mock",
            sequential_responses=[
                write(1),
                *[
                    {
                        "type": "tool_call",
                        "tool": "read_file",
                        "args": {"path": "sample.py", "start_line": line, "end_line": line},
                    }
                    for line in range(2, 11)
                ],
                write(42),
                {"type": "final", "content": "Applied the remaining edit."},
            ],
        )
        architect = create_llm(
            "mock",
            sequential_responses=[
                {
                    "type": "tool_call",
                    "tool": "coder",
                    "args": {"task": "Change sample.py value to 42."},
                },
                {
                    "type": "tool_call",
                    "tool": "run_check",
                    "args": {"check_id": "python-tests", "phase": "verify"},
                },
                {"type": "final", "content": "Changed and checked sample.py."},
            ],
        )
        result = await workflow_fixtures.WorkflowControlTests.run_workflow(self, architect, coder)
        self.assertEqual(result["status"], "completed", result["answer"])
        self.assertEqual(
            (self.root / "sample.py").read_text(), source.replace("value = 0", "value = 42")
        )
        self.assertEqual(coder._current_seq_idx, 12)
        self.assertEqual(len(result["approval_decisions"]), 3)
        continuing = [e for e in result["run_events"] if e["type"] == "worker.continuing"]
        self.assertEqual(len(continuing), 1)
        self.assertIn("completed_action_ids", continuing[0]["payload"]["handoff"])
        writes = [
            e
            for e in result["run_events"]
            if e["type"] == "action.completed"
            and e["payload"].get("result", {}).get("state") == "applied"
        ]
        self.assertEqual(len(writes), 2)
        self.assertEqual(len({e["run_id"] for e in writes}), 1)
        self.assertFalse(any(e["type"] == "action.failed" for e in result["run_events"]))

    async def test_read_only_slice_does_not_gain_another_model_call_or_claim_completion(self):
        (self.root / "sample.py").write_text("value = 0\n" + "# source\n" * 12)
        coder = create_llm(
            "mock",
            sequential_responses=[
                {
                    "type": "tool_call",
                    "tool": "read_file",
                    "args": {"path": "sample.py", "start_line": line, "end_line": line},
                }
                for line in range(1, 12)
            ],
        )
        architect = create_llm(
            "mock",
            sequential_responses=[
                {
                    "type": "tool_call",
                    "tool": "coder",
                    "args": {"task": "Change sample.py value to 42."},
                },
                {"type": "final", "content": "This must not be requested."},
            ],
        )
        result = await workflow_fixtures.WorkflowControlTests.run_workflow(self, architect, coder)
        self.assertNotEqual(result["status"], "completed", result["answer"])
        self.assertEqual(coder._current_seq_idx, 9)
        self.assertEqual(architect._current_seq_idx, 1)
        self.assertFalse(result["completion_validation"]["satisfied"])
        self.assertFalse(result["approval_requests"])
        self.assertFalse(any(e["type"] == "worker.continuing" for e in result["run_events"]))
