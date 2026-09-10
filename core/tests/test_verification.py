"""Exercise real subprocesses through ProtoLink's verification policy boundary."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

from protolink import (
    ActionDeniedError,
    ApprovalDecision,
    BudgetExceededError,
    RunBudget,
    RunContext,
    Task,
    TaskCancellationRequest,
)

from protoagent_core.agents.verifier import create_verifier_agent
from protoagent_core.verification import MAX_OUTPUT_BYTES, VerificationEvidence, run_command


class VerificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_denial_never_starts_a_process(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            marker = Path(root) / "ran.txt"

            async def deny(request, _context):
                self.assertEqual(request.action.capabilities, frozenset({"shell.execute"}))
                self.assertEqual(request.action.artifacts[0].media_type, "text/plain")
                self.assertIn("host access", request.action.description)
                return ApprovalDecision(approved=False, request_id=request.request_id)

            agent = create_verifier_agent(
                workspace=root, transport="runtime", approval_handler=deny
            )
            with self.assertRaises(ActionDeniedError):
                await agent.call_tool_in_context(
                    "run_command",
                    RunContext(),
                    argv=[sys.executable, "-c", f"open({str(marker)!r}, 'w').write('ran')"],
                )
            self.assertFalse(marker.exists())

    async def test_approved_command_returns_real_exit_status_and_streamed_output(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            evidence = VerificationEvidence()
            output: list[str] = []
            agent = create_verifier_agent(
                workspace=root,
                transport="runtime",
                approval_handler=lambda *_: True,
                evidence=evidence,
                on_output=output.append,
            )
            self.assertIsNone(agent.llm)
            result = await agent.call_tool_in_context(
                "run_command",
                RunContext(),
                argv=[sys.executable, "-c", "print('one failure'); raise SystemExit(7)"],
            )
            self.assertEqual(result["exit_code"], 7)
            self.assertFalse(result["success"])
            self.assertIn("one failure", "".join(output))
            self.assertEqual(evidence.report()["status"], "failed")
            evidence.changed()
            self.assertEqual(evidence.report()["status"], "not-run")

    async def test_output_limit_is_drained_and_terminal_controls_are_removed(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            result = await run_command(
                [sys.executable, "-c", "print('\\x1b[31m' + 'x' * 200000)"], workspace=root
            )
            self.assertEqual(result["exit_code"], 0)
            self.assertTrue(result["truncated"])
            self.assertLessEqual(len(result["output"].encode()), MAX_OUTPUT_BYTES)
            self.assertNotIn("\x1b", result["output"])

    async def test_timeout_kills_command_before_delayed_file_write(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            result = await run_command(
                [
                    sys.executable,
                    "-c",
                    "import time; time.sleep(2); open('late', 'w').write('bad')",
                ],
                timeout_seconds=1,
                workspace=root,
            )
            self.assertTrue(result["timed_out"])
            await asyncio.sleep(1.2)
            self.assertFalse((Path(root) / "late").exists())

    async def test_cancellation_uses_protolink_task_control_and_reaps_process(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            started = asyncio.Event()
            agent = create_verifier_agent(
                workspace=root,
                transport="runtime",
                approval_handler=lambda *_: True,
                on_output=lambda _: started.set(),
            )
            task = Task.create_tool_call(
                tool_name="run_command",
                args={
                    "argv": [
                        sys.executable,
                        "-u",
                        "-c",
                        "import time; print('ready'); time.sleep(2); open('late', 'w').write('bad')",
                    ]
                },
            )
            running = asyncio.create_task(agent.run_task(task))
            await asyncio.wait_for(started.wait(), 3)
            await agent.cancel_task(TaskCancellationRequest(id=task.id, reason="test cancellation"))
            result = await asyncio.wait_for(running, 3)
            self.assertEqual(result.state.value, "canceled")
            await asyncio.sleep(2.1)
            self.assertFalse((Path(root) / "late").exists())

    async def test_native_direct_task_budget_prevents_command_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            agent = create_verifier_agent(
                workspace=root, transport="runtime", approval_handler=lambda *_: True
            )
            task = Task.create_tool_call(
                tool_name="run_command",
                args={"argv": [sys.executable, "-c", "open('ran', 'w').write('bad')"]},
            )
            RunContext(budget=RunBudget(max_tool_calls=0)).attach_to_task(task)
            with self.assertRaises(BudgetExceededError):
                await agent.run_task(task)
            self.assertFalse((Path(root) / "ran").exists())

    async def test_invalid_directory_and_timeout_are_rejected_before_approval(self) -> None:
        with tempfile.TemporaryDirectory() as root:

            def never_approve(*_):
                self.fail("invalid arguments reached approval")

            agent = create_verifier_agent(
                workspace=root, transport="runtime", approval_handler=never_approve
            )
            for kwargs in (
                {"cwd": ".."},
                {"cwd": "\x1b[2J"},
                {"timeout_seconds": 0},
                {"timeout_seconds": 601},
            ):
                with self.assertRaises(ValueError):
                    await agent.call_tool_in_context(
                        "run_command", RunContext(), argv=[sys.executable], **kwargs
                    )

    async def test_argv_is_not_interpreted_by_a_shell(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            result = await run_command(
                [sys.executable, "-c", "import sys; print(sys.argv[1])", "$(touch injected)"],
                workspace=root,
            )
            self.assertIn("$(touch injected)", result["output"])
            self.assertFalse((Path(root) / "injected").exists())

    @unittest.skipUnless(os.name == "posix", "process groups are POSIX-only")
    async def test_timeout_stops_descendants_after_parent_exit(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            child = "import time; time.sleep(2); open('orphan', 'w').write('bad')"
            parent = f"import subprocess,sys; subprocess.Popen([sys.executable, '-c', {child!r}])"
            result = await run_command(
                [sys.executable, "-c", parent], workspace=root, timeout_seconds=1
            )
            self.assertTrue(result["timed_out"])
            await asyncio.sleep(1.2)
            self.assertFalse((Path(root) / "orphan").exists())


class EvidenceTests(unittest.TestCase):
    def test_successful_retry_replaces_failure_for_same_command_only(self) -> None:
        evidence = VerificationEvidence()
        for command, success in (("test", False), ("lint", False), ("test", True)):
            evidence.record(
                {"command": command, "cwd": "/project", "success": success}, evidence.revision()
            )
        self.assertEqual(evidence.report()["status"], "failed")
        evidence.record(
            {"command": "lint", "cwd": "/project", "success": True}, evidence.revision()
        )
        self.assertEqual(evidence.report()["status"], "passed")
        old_revision = evidence.revision()
        evidence.changed()
        evidence.record({"command": "test", "cwd": "/project", "success": True}, old_revision)
        self.assertEqual(evidence.report()["status"], "not-run")
