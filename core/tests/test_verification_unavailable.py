"""Missing verification setup ends quietly while retaining real native evidence."""

import sys

from protolink import RunReport, Task, TaskState
from runtime_support import NativeRuntimeCase

from protoagent_core.run_contracts import infer_run_contract
from protoagent_core.task_record import CheckSpec
from protoagent_core.verification import (
    validate_completion,
    verification_setup_issue,
    verification_summary,
)


class VerificationUnavailableTests(NativeRuntimeCase):
    async def test_missing_runner_retains_failed_receipt_and_does_not_claim_success(self):
        written, checked = await self.missing_runner()
        report = RunReport.from_events((*written.report.events, *checked.report.events))
        accepted = await validate_completion(
            infer_run_contract("Change sample.py"), checked.task, report, self.attempt, self.broker
        )
        self.assertEqual(accepted.completion["outcome"], "unverified")
        self.assertFalse(accepted.completion["satisfied"])
        self.assertFalse(accepted.completion["verified"])
        self.assertEqual(accepted.verification["status"], "unavailable")
        self.assertEqual(accepted.verification["results"][0]["exit_code"], 1)
        self.assertEqual(accepted.verification["setup_issues"][0]["code"], "runner_unavailable")
        summary = verification_summary(accepted.verification)
        self.assertEqual(len(summary.splitlines()), 1)
        self.assertNotIn(sys.executable, summary)

    async def test_unavailable_setup_does_not_hide_stale_edits(self):
        written, checked = await self.missing_runner()
        (self.root / "sample.py").write_text("value = 99\n")
        report = RunReport.from_events((*written.report.events, *checked.report.events))
        accepted = await validate_completion(
            infer_run_contract("Change sample.py"), checked.task, report, self.attempt, self.broker
        )
        self.assertEqual(accepted.verification["status"], "stale")
        self.assertNotEqual(accepted.completion["outcome"], "unverified")
        self.assertFalse(accepted.completion["satisfied"])

    async def test_missing_runner_does_not_downgrade_canceled_task(self):
        written, checked = await self.missing_runner()
        task = Task.create_infer("Change sample.py", context=self.context.cancel("User canceled"))
        task.update_state(TaskState.CANCELED)
        report = RunReport.from_events((*written.report.events, *checked.report.events))
        accepted = await validate_completion(
            infer_run_contract("Change sample.py"), task, report, self.attempt, self.broker
        )
        self.assertEqual(accepted.completion["outcome"], "canceled")
        self.assertFalse(accepted.completion["satisfied"])
        self.assertNotEqual(accepted.verification["status"], "unavailable")

    async def missing_runner(self):
        argv = (sys.executable, "-m", "_protoagent_missing_runner_fixture")
        self.attempt.record.checks["tests"] = CheckSpec("tests", argv, str(self.root), {})
        self.attempt.record.selected = ("tests",)
        written = await self.tool(
            self.coder, "create_file", {"path": "sample.py", "content": "value = 42\n"}
        )
        checked = await self.tool(
            self.verifier, "run_check", {"check_id": "tests", "phase": "verify"}
        )
        return written, checked

    def test_missing_local_module_and_interrupted_checks_are_not_setup_exceptions(self):
        (self.root / "server.py").write_text("value = 42\n")
        result = {"exit_code": 1, "stderr": "ModuleNotFoundError: No module named 'server'\n"}
        self.assertIsNone(
            verification_setup_issue(result, {"argv": [sys.executable, "test.py"]}, self.root)
        )
        result = {
            "exit_code": 1,
            "stderr": "ModuleNotFoundError: No module named 'pytest'\n",
            "timed_out": True,
        }
        self.assertIsNone(
            verification_setup_issue(result, {"argv": [sys.executable, "-m", "pytest"]}, self.root)
        )

    def test_success_and_failure_summaries_remain_one_line(self):
        for status in ("passed", "failed", "stale", "deferred", "unverified"):
            with self.subTest(status=status):
                text = verification_summary(
                    {
                        "status": status,
                        "latest": [{"exit_code": 1 if status == "failed" else 0}],
                        "results": [],
                    }
                )
                self.assertEqual(len(text.splitlines()), 1)
                self.assertLess(len(text), 100)

    def test_pytest_collection_exception_is_setup_but_printed_test_text_is_not(self):
        args = {"argv": [sys.executable, "-m", "pytest"]}
        result = {
            "exit_code": 2,
            "stdout": "ERROR collecting test_server.py\nE   ModuleNotFoundError: No module named 'fastapi'\n",
        }
        issue = verification_setup_issue(result, args, self.root)
        self.assertEqual(issue["module"], "fastapi")
        self.assertEqual(issue["code"], "dependency_unavailable")
        result["stdout"] = "No module named fastapi\nAssertionError: unexpected text\n"
        self.assertIsNone(verification_setup_issue(result, args, self.root))
