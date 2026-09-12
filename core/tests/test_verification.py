"""Execute real, provider-free commands through the native verifier and RunHandle."""

import asyncio
import sys

from protolink import ApprovalDecision, RunBudget
from runtime_support import NativeRuntimeCase


class VerificationTests(NativeRuntimeCase):
    def arguments(self, code, **kwargs):
        return {
            "argv": [sys.executable, "-c", code],
            "cwd": str(self.root),
            "env": {},
            "timeout_seconds": 3,
            "max_output_bytes": 32768,
            **kwargs,
        }

    async def test_denial_precedes_process_effect(self):
        result = await self.tool(
            self.verifier,
            "execute_command",
            self.arguments("open('marker','w').write('bad')"),
            approved=False,
        )
        self.assertEqual(result.status, "failed")
        self.assertFalse((self.root / "marker").exists())
        record = self.broker.records(self.authorization.scope)[0]
        self.assertEqual(record.request.action.capabilities, frozenset({"process.execute"}))
        self.assertIn("host process", record.request.action.payload["boundary"])

    async def test_native_exit_output_and_explicit_environment(self):
        result = await self.tool(
            self.verifier,
            "execute_command",
            self.arguments(
                "import os; print(os.getenv('MY_SETTING')); print('failed'); raise SystemExit(7)",
                env={"MY_SETTING": "exact"},
            ),
        )
        self.assertEqual(result.output["exit_code"], 7)
        self.assertEqual(result.output["stdout"], "exact\nfailed\n")
        self.assertTrue(any(event.type == "process.output" for event in result.report.events))
        self.assertIsNone(self.verifier.llm)

    async def test_output_cap_and_display_redaction(self):
        result = await self.tool(
            self.verifier,
            "execute_command",
            self.arguments("print('\\x1b[31m'+'x'*200000)", max_output_bytes=1024),
        )
        self.assertEqual(result.output["exit_code"], 0)
        self.assertTrue(result.output["truncated"])
        self.assertLessEqual(len(result.output["stdout"].encode()), 1024)
        display = self.redaction.redact(result.output)
        self.assertNotIn("\x1b", display["stdout"])

    async def test_native_timeout_and_cancellation_clean_up_process(self):
        for cancel in (False, True):
            handle = self.start_tool(
                self.verifier,
                "execute_command",
                self.arguments(
                    "import time; print('ready',flush=True); time.sleep(0.5); open('late','w').write('bad')",
                    timeout_seconds=3 if cancel else 0.1,
                ),
            )
            (record,) = await self.pending()
            self.broker.resolve(
                ApprovalDecision(True, record.request.request_id),
                scope=self.authorization.scope,
                fingerprint=record.fingerprint,
            )
            async for event in handle.events():
                if cancel and event.type == "process.output":
                    await handle.cancel("user canceled")
            result = await handle.result()
            self.assertEqual(result.status, "canceled" if cancel else "completed")
            if not cancel:
                self.assertTrue(result.output["timed_out"])
            await asyncio.sleep(0.55)
            self.assertFalse((self.root / "late").exists())

    async def test_native_budget_denies_before_process_effect(self):
        context = self.context.child(agent_name="verifier")
        context.budget = RunBudget(max_tool_calls=0)
        handle = self.start_tool(
            self.verifier, "execute_command", self.arguments("open('marker','w')"), context=context
        )
        (record,) = await self.pending()
        self.broker.resolve(
            ApprovalDecision(True, record.request.request_id),
            scope=self.authorization.scope,
            fingerprint=record.fingerprint,
        )
        result = await handle.result()
        self.assertEqual(result.status, "failed")
        self.assertFalse((self.root / "marker").exists())
        self.assertFalse(any(event.type == "action.started" for event in result.report.events))

    async def test_invalid_limits_and_outside_cwd_are_denied_before_approval(self):
        for overrides in (
            {"cwd": str(self.root.parent)},
            {"timeout_seconds": 601},
            {"max_output_bytes": 32769},
        ):
            result = await self.start_tool(
                self.verifier, "execute_command", self.arguments("print('bad')", **overrides)
            ).result()
            self.assertEqual(result.status, "failed")
        self.assertEqual(self.broker.records(self.authorization.scope), ())

    async def test_argv_has_no_implicit_shell_expansion(self):
        args = self.arguments("import sys; print(sys.argv[1])")
        args["argv"].append("$(touch injected)")
        result = await self.tool(self.verifier, "execute_command", args)
        self.assertIn("$(touch injected)", result.output["stdout"])
        self.assertFalse((self.root / "injected").exists())
