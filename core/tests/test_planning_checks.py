"""Missing suites and model planning mistakes must not crash the coding harness."""

import json
from unittest.mock import patch

from protolink import Agent, CapabilityPolicy, RunHandle, Task
from protolink.llms.factory import create_llm
from runtime_support import NativeRuntimeCase

from protoagent_core.request_budget import configure_agent_context
from protoagent_core.run_contracts import infer_run_contract
from protoagent_core.task_record import (
    PYTHON_BOOTSTRAP_SOURCE,
    add_task_tools,
    discover_checks,
)
from protoagent_core.verification import validate_completion, verification_summary


class PlanningCheckTests(NativeRuntimeCase):
    def discover(self):
        record = self.attempt.record
        record.checks = discover_checks(str(self.root))
        record.selected = tuple(record.checks)
        return record

    async def acceptance(self, *results):
        from protolink import RunReport

        return await validate_completion(
            infer_run_contract("Fix sample.py"),
            results[-1].task,
            RunReport.from_events([event for result in results for event in result.report.events]),
            self.attempt,
            self.broker,
        )

    def test_small_python_project_gets_a_predeclared_runner_without_importing_it(self):
        (self.root / "server.py").write_text(
            "raise AssertionError('discovery must not import source')\n"
        )
        record = self.discover()
        check = record.checks["python-tests"]
        self.assertEqual(check.source, PYTHON_BOOTSTRAP_SOURCE)
        self.assertEqual(
            check.argv[1:], ("-m", "unittest", "discover", "-s", ".", "-p", "test_*.py", "-q")
        )
        self.assertEqual(record.snapshot()["bootstrap_checks"], ["python-tests"])
        self.assertEqual(record.snapshot()["available_check_ids"], ["python-tests"])
        planned = record.plan(["server.py", "test_server.py"], ["Preserve endpoint behavior"])
        self.assertEqual(planned["required_checks"], ["python-tests"])

    async def test_plan_feedback_allows_correction_in_the_same_native_run(self):
        (self.root / "sample.py").write_text("value = 0\n")
        record = self.discover()
        responses = [
            {
                "type": "tool_call",
                "tool": "plan_task",
                "args": {"paths": ["sample.py"], "criteria": ["Value is 42"], "check_ids": []},
            },
            {
                "type": "tool_call",
                "tool": "plan_task",
                "args": {"paths": ["sample.py", "test_sample.py"], "criteria": ["Value is 42"]},
            },
            {
                "type": "final",
                "content": "Plan includes the predefined runner and a behavior regression.",
            },
        ]
        inputs = []
        llm = create_llm("mock", sequential_responses=responses)
        acquire = llm.call

        def observe(history):
            inputs.append(history.to_list())
            return acquire(history)

        llm.call = observe
        agent = Agent(
            name="planner", llm=llm, policy=CapabilityPolicy({"task.manage": "allow"}), verbosity=0
        )
        add_task_tools(agent, record, "architect")
        configure_agent_context(agent, record, fallback_window=8192, compact_protocol=True)
        result = await RunHandle.start(agent, Task.create_infer("Plan a value fix")).result()
        self.assertEqual(result.status, "completed", result.error)
        self.assertEqual(len(inputs), 3)
        self.assertIn('"available_check_ids": ["python-tests"]', inputs[0][0]["content"])
        self.assertIn('"success": false', json.dumps(inputs[1]).replace('\\"', '"'))
        self.assertEqual(record.selected, ("python-tests",))
        self.assertEqual(
            record.allowed_paths, (str(self.root / "sample.py"), str(self.root / "test_sample.py"))
        )
        self.assertFalse(any(event.type == "action.failed" for event in result.report.events))
        self.assertEqual((self.root / "sample.py").read_text(), "value = 0\n")

    def test_bootstrap_does_not_add_requirements_to_a_project_with_existing_checks(self):
        (self.root / "package.json").write_text('{"scripts":{"test":"node --test"}}')
        (self.root / "tool.py").write_text("value = 0\n")
        with patch("protoagent_core.task_record.shutil.which", return_value="/usr/bin/npm"):
            record = self.discover()
        self.assertEqual(record.selected, ("npm-test",))
        self.assertEqual(record.snapshot()["bootstrap_checks"], [])

    async def test_bootstrap_baseline_then_real_regression_and_fix_can_verify(self):
        (self.root / "sample.py").write_text("def answer():\n    return 0\n")
        record = self.discover()
        record.plan(["sample.py", "test_sample.py"], ["answer returns 42"])
        empty = await self.tool(
            self.verifier, "run_check", {"check_id": "python-tests", "phase": "baseline"}
        )
        self.assertIn(
            "Ran 0 tests",
            empty.output[0].content["stderr"]
            if isinstance(empty.output, list)
            else str(empty.output),
        )
        regression = await self.tool(
            self.coder,
            "create_file",
            {
                "path": str(self.root / "test_sample.py"),
                "content": "import unittest\nfrom sample import answer\nclass Regression(unittest.TestCase):\n    def test_answer(self):\n        self.assertEqual(answer(), 42)\n",
            },
        )
        failing = await self.tool(
            self.verifier, "run_check", {"check_id": "python-tests", "phase": "baseline"}
        )
        written = await self.tool(
            self.coder,
            "replace_file",
            {"path": str(self.root / "sample.py"), "content": "def answer():\n    return 42\n"},
        )
        checked = await self.tool(self.verifier, "run_check", {"check_id": "python-tests"})
        accepted = await self.acceptance(empty, regression, failing, written, checked)
        self.assertTrue(accepted.completion["satisfied"], accepted.completion)
        self.assertEqual(accepted.verification["status"], "passed")
        self.assertEqual(accepted.verification["latest"][0]["check_id"], "python-tests")
        self.assertIn("Ran 1 test", accepted.verification["latest"][0]["stderr"])

    async def test_zero_tests_still_cannot_verify_bootstrapped_edits(self):
        (self.root / "sample.py").write_text("value = 0\n")
        self.discover().plan(["sample.py"], ["Value is 42"])
        written = await self.tool(
            self.coder,
            "replace_file",
            {"path": str(self.root / "sample.py"), "content": "value = 42\n"},
        )
        checked = await self.tool(self.verifier, "run_check", {"check_id": "python-tests"})
        accepted = await self.acceptance(written, checked)
        self.assertFalse(accepted.completion["satisfied"])
        self.assertFalse(accepted.completion["verified"])
        self.assertTrue(accepted.verification["latest"][0]["empty_test_suite"])
        self.assertIn("zero tests ran", verification_summary(accepted.verification))

    async def test_explicit_empty_configuration_preserved_and_edits_remain_unverified(self):
        (self.root / ".protoagent").mkdir()
        (self.root / ".protoagent" / "project.json").write_text('{"checks":[]}')
        (self.root / "sample.py").write_text("value = 0\n")
        record = self.discover()
        self.assertEqual(record.checks, {})
        record.plan(["sample.py"], ["Value is 42"], [])
        written = await self.tool(
            self.coder,
            "replace_file",
            {"path": str(self.root / "sample.py"), "content": "value = 42\n"},
        )
        accepted = await self.acceptance(written)
        self.assertTrue(accepted.completion["applied"])
        self.assertFalse(accepted.completion["satisfied"])
        self.assertEqual(accepted.verification["status"], "unverified")

    def test_unknown_ids_and_frozen_plans_return_feedback_without_weakening_requirements(self):
        (self.root / "sample.py").write_text("value = 0\n")
        record = self.discover()
        agent = Agent(name="planner", verbosity=0)
        add_task_tools(agent, record, "architect")
        tool = agent.tools["plan_task"].func
        original = record.snapshot()
        rejected = tool(["sample.py"], ["Ignore tests"], ["invented"])
        self.assertFalse(rejected["success"])
        self.assertEqual(rejected["code"], "unknown_check_ids")
        self.assertEqual(original, record.snapshot())
        record.frozen = True
        frozen = record.snapshot()
        self.assertEqual(
            tool(["sample.py"], ["Ignore tests"], ["python-tests"])["code"], "plan_frozen"
        )
        self.assertEqual(frozen, record.snapshot())
