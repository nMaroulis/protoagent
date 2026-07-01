from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from protoagent_core import config as config_module
from protoagent_core.quality_eval import EVAL_TASKS, run_quality_eval, score_response


class QualityEvalTests(unittest.TestCase):
    def test_read_only_task_scores_explorer_without_coder(self) -> None:
        task = next(item for item in EVAL_TASKS if item.id == "explain-cancellation-path")
        response = {
            "status": "answered",
            "answer": "See core/protoagent_core/runtime.py and cli/src/terminal_ui.rs.",
            "run_events": [
                {
                    "type": "action.started",
                    "agent_name": "architect",
                    "payload": {
                        "llm_event_type": "agent_call_start",
                        "metadata": {"agent": "explorer"},
                    },
                },
                {
                    "type": "action.started",
                    "agent_name": "explorer",
                    "payload": {
                        "llm_event_type": "tool_start",
                        "metadata": {"tool": "read_file"},
                    },
                },
            ],
            "approval_requests": [],
        }

        score = score_response(response, task)

        self.assertEqual(score["score"], 1.0)
        self.assertTrue(score["observations"]["used_explorer"])
        self.assertFalse(score["observations"]["used_coder"])

    def test_change_task_scores_coder_docs_and_tests(self) -> None:
        task = next(item for item in EVAL_TASKS if item.id == "approval-denial-regression")
        response = {
            "status": "answered",
            "answer": "Updated core/tests/test_runtime_integration.py.",
            "run_events": [
                {
                    "type": "action.started",
                    "agent_name": "architect",
                    "payload": {
                        "llm_event_type": "agent_call_start",
                        "metadata": {"agent": "explorer"},
                    },
                },
                {
                    "type": "action.started",
                    "agent_name": "architect",
                    "payload": {
                        "llm_event_type": "agent_call_start",
                        "metadata": {"agent": "coder"},
                    },
                },
            ],
            "approval_requests": [
                {
                    "request_id": "approval_1",
                    "action": {
                        "metadata": {"path": "core/tests/test_runtime_integration.py"},
                        "payload": {
                            "arguments": {"path": "core/tests/test_runtime_integration.py"}
                        },
                    },
                }
            ],
        }

        score = score_response(response, task)

        self.assertTrue(score["observations"]["used_coder"])
        self.assertTrue(score["observations"]["tests_touched"])
        self.assertEqual(score["score"], 1.0)

    def test_plan_mode_builds_profile_task_matrix_without_model_calls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_dir = root / "config"
            config_path = config_dir / "config.json"
            with (
                patch.object(config_module, "CONFIG_DIR", config_dir),
                patch.object(config_module, "CONFIG_PATH", config_path),
            ):
                report = run_quality_eval(
                    workspace=str(root),
                    profiles="small,api",
                    task_ids="explain-cancellation-path",
                    mode="plan",
                )

        self.assertEqual(report["summary"]["profile_count"], 2)
        self.assertEqual(report["summary"]["task_count"], 1)
        self.assertEqual(report["summary"]["run_count"], 2)
        self.assertIsNone(report["summary"]["score"])


if __name__ == "__main__":
    unittest.main()
