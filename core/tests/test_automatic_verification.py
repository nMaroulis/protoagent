"""Verification is a single native software step, independent of model choices."""

import asyncio
import json
import sys
from unittest.mock import patch

import test_workflow_control as workflow_fixtures
from protolink.llms.factory import create_llm
from runtime_support import NativeRuntimeCase


class AutomaticVerificationTests(NativeRuntimeCase):
    def prepare_source(self):
        (self.root / "sample.py").write_text("value = 0\n")
        (self.root / "test_sample.py").write_text(
            "import unittest\nimport sample\nclass Regression(unittest.TestCase):\n"
            "    def test_value(self):\n        self.assertEqual(sample.value, 42)\n"
        )

    async def run_edit(self, value=42, *, verifier_enabled=True):
        architect = create_llm(
            "mock",
            sequential_responses=[
                {
                    "type": "tool_call",
                    "tool": "coder",
                    "args": {"task": "Set sample.py value to 42."},
                },
                {"type": "final", "content": "Updated sample.py."},
            ],
        )
        coder = create_llm(
            "mock", sequential_responses=workflow_fixtures.WorkflowControlTests.assignment(value)
        )
        result = await workflow_fixtures.WorkflowControlTests.run_workflow(
            self, architect, coder, verifier_enabled=verifier_enabled
        )
        self.assertEqual(architect._current_seq_idx, 2)
        self.assertEqual(coder._current_seq_idx, 2)
        return result

    async def test_checks_run_once_after_edits_without_model_check_calls(self):
        self.prepare_source()
        result = await self.run_edit()
        self.assertEqual(result["status"], "completed", result["answer"])
        self.assertEqual(result["verification"]["status"], "passed")
        self.assertEqual(len(result["verification"]["results"]), 1)
        self.assertEqual(result["verification"]["results"][0]["phase"], "verify")
        self.assertEqual(len(result["approval_requests"]), 2)
        effects = [
            event
            for event in result["run_events"]
            if event["type"] == "action.completed"
            and isinstance(event["payload"].get("action"), dict)
        ]
        self.assertEqual(
            [event["payload"]["action"]["name"] for event in effects],
            ["replace_file", "execute_command"],
        )
        self.assertFalse(
            any(event["type"].startswith("user_input.") for event in result["run_events"])
        )

    async def test_missing_pytest_does_not_block_edits_or_start_setup_conversation(self):
        self.prepare_source()
        directory = self.root / ".protoagent"
        directory.mkdir()
        (directory / "project.json").write_text(
            json.dumps(
                {
                    "checks": [
                        {
                            "id": "pytest",
                            "argv": [sys.executable, "-I", "-S", "-m", "pytest", ".", "-q"],
                            "cwd": ".",
                            "env": {},
                            "paths": ["sample.py", "test_sample.py"],
                        }
                    ]
                }
            )
        )
        result = await self.run_edit()
        self.assertEqual(result["status"], "unverified", result["answer"])
        self.assertEqual(result["verification"]["status"], "unavailable")
        self.assertEqual(result["verification"]["setup_issues"][0]["module"], "pytest")
        self.assertEqual(len(result["verification"]["results"]), 1)
        self.assertEqual((self.root / "sample.py").read_text(), "value = 42\n")
        self.assertFalse(
            any(event["type"].startswith("user_input.") for event in result["run_events"])
        )

    async def test_real_test_failure_is_reported_without_automatic_repairs(self):
        self.prepare_source()
        result = await self.run_edit(1)
        self.assertEqual(result["status"], "incomplete", result["answer"])
        self.assertEqual(result["verification"]["status"], "failed")
        self.assertEqual(len(result["verification"]["results"]), 1)
        self.assertEqual((self.root / "sample.py").read_text(), "value = 1\n")
        self.assertFalse(
            any(event["type"] == "workflow.repair.requested" for event in result["run_events"])
        )

    async def test_disabled_verifier_skips_discovery_and_commands_but_preserves_edits(self):
        self.prepare_source()
        with patch(
            "protoagent_core.task_record.discover_checks", side_effect=AssertionError("Disabled")
        ):
            result = await self.run_edit(verifier_enabled=False)
        self.assertEqual(result["status"], "unverified", result["answer"])
        self.assertEqual(result["verification"]["status"], "disabled")
        self.assertFalse(result["verification"]["results"])
        self.assertEqual(len(result["approval_requests"]), 1)
        self.assertIn("/agents verifier on", result["answer"])

    async def test_project_without_tests_does_not_require_generating_test_files(self):
        (self.root / "sample.py").write_text("value = 0\n")
        result = await self.run_edit()
        self.assertEqual(result["status"], "unverified", result["answer"])
        self.assertEqual(result["verification"]["status"], "not_configured")
        self.assertFalse(result["verification"]["results"])
        self.assertFalse(list(self.root.glob("test_*.py")))

    async def test_explicit_verification_needs_no_model_requests(self):
        self.prepare_source()
        (self.root / "sample.py").write_text("value = 42\n")

        def no_model(*args):
            raise AssertionError("Verification must not call a model")

        result = await workflow_fixtures.WorkflowControlTests.run_workflow(
            self,
            create_llm("mock", response_callback=no_model),
            create_llm("mock", response_callback=no_model),
            prompt="run the tests",
        )
        self.assertEqual(result["status"], "completed", result["answer"])
        self.assertEqual(result["verification"]["status"], "passed")
        self.assertEqual(len(result["approval_requests"]), 1)

    async def test_explicit_checks_work_without_a_selected_or_running_model(self):
        from protoagent_core import agent_engine, config
        from protoagent_core.approvals import configure_approvals

        self.prepare_source()
        (self.root / "sample.py").write_text("value = 42\n")
        config.save_config(config.default_config())
        configure_approvals("auto", self.root)
        with (
            patch.dict("os.environ", {}),
            patch(
                "protoagent_core.agent_engine.model_startup_problem",
                side_effect=AssertionError("No model preflight"),
            ),
            patch(
                "protoagent_core.agents.common.create_llm_from_config",
                side_effect=AssertionError("No LLM construction"),
            ),
            patch(
                "protoagent_core.agent_engine._context_pack_for_prompt",
                side_effect=AssertionError("No Context Loom"),
            ),
        ):
            result = json.loads(
                await asyncio.to_thread(
                    agent_engine.process_prompt,
                    "run the tests",
                    str(self.root),
                    None,
                    str(self.bridge.progress_path),
                )
            )
        self.assertEqual(result["status"], "answered", result["answer"])
        self.assertEqual(result["verification"]["status"], "passed")

    async def test_post_edit_check_cannot_reset_the_root_tool_budget(self):
        self.prepare_source()
        architect = create_llm(
            "mock",
            sequential_responses=[
                {
                    "type": "tool_call",
                    "tool": "coder",
                    "args": {"task": "Set sample.py value to 42."},
                },
                {"type": "final", "content": "Updated sample.py."},
            ],
        )
        # The workflow tool and file write consume the two calls. Delegation
        # has its own native child accounting; it is not another tool effect.
        with patch.dict("os.environ", {"PROTOAGENT_RUN_MAX_TOOL_CALLS": "2"}):
            result = await workflow_fixtures.WorkflowControlTests.run_workflow(
                self,
                architect,
                create_llm(
                    "mock",
                    sequential_responses=workflow_fixtures.WorkflowControlTests.assignment(42),
                ),
            )
        self.assertNotEqual(result["status"], "completed", result["answer"])
        self.assertEqual((self.root / "sample.py").read_text(), "value = 42\n")
        self.assertFalse(result["verification"]["results"])
        self.assertFalse(result["completion_validation"]["verified"])
