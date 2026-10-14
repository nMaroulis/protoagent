"""Project preferences resolve exact native approvals without bypassing guards."""

import asyncio
import hashlib
import json
import sys
from contextlib import suppress

from protolink import ApprovalDecision
from runtime_support import NativeRuntimeCase

from protoagent_core import config
from protoagent_core.approvals import approval_mode, configure_approvals
from protoagent_core.runtime_bridge import RuntimeBridge


class ApprovalTests(NativeRuntimeCase):
    async def auto_tool(self, agent, name, args, mode="edits", bridge=None):
        bridge = bridge or self.bridge
        bridge.bind(self.broker, self.authorization, self.redaction, approval_mode=mode)
        handle = self.start_tool(agent, name, args)
        controls = asyncio.create_task(bridge.serve(handle))
        try:
            return await asyncio.wait_for(handle.result(), 3)
        finally:
            controls.cancel()
            with suppress(asyncio.CancelledError):
                await controls

    def test_project_preferences_are_explicit_canonical_and_fail_closed(self):
        self.assertEqual(approval_mode(self.root), "ask")
        configure_approvals("edits", self.root)
        alias = self.root.parent / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        self.assertEqual(approval_mode(alias), "edits")
        other = self.root.parent / "other"
        other.mkdir()
        self.assertEqual(approval_mode(other), "ask")
        with self.assertRaises(ValueError):
            configure_approvals("yes", self.root)
        self.assertEqual(approval_mode(self.root, {"approval_modes": []}), "ask")
        self.assertEqual(
            approval_mode(self.root, {"approval_modes": {str(self.root): True}}), "ask"
        )
        self.assertEqual(config.CONFIG_PATH.stat().st_mode & 0o777, 0o600)
        configure_approvals("ask", self.root)
        self.assertEqual(approval_mode(self.root), "ask")

    async def test_auto_edits_apply_and_retain_native_broker_records(self):
        target = self.root / "auto.txt"
        result = await self.auto_tool(
            self.coder, "create_file", {"path": str(target), "content": "one"}
        )
        self.assertEqual(result.status, "completed", result.error)
        self.assertEqual(target.read_text(), "one")
        (record,) = self.broker.records(self.authorization.scope)
        self.assertEqual(record.status, "approved")
        self.assertEqual(record.decision.decided_by, "protoagent-auto")
        self.assertEqual(record.fingerprint, record.request.action.fingerprint)
        self.assertFalse(self.bridge.request_path.exists())
        self.assertEqual(self.checkpoints.get(result.output["change_id"]).state, "applied")

    async def test_explicit_auto_preference_works_without_interactive_frontend(self):
        result = await self.auto_tool(
            self.coder,
            "create_file",
            {"path": str(self.root / "headless"), "content": "yes"},
            bridge=RuntimeBridge(None),
        )
        self.assertEqual(result.status, "completed", result.error)

    async def test_exact_edit_auto_accepts_native_replacement_and_returns_revision(self):
        target = self.root / "source.py"
        target.write_text("value = 0\n")
        result = await self.auto_tool(
            self.coder,
            "edit_file",
            {
                "path": str(target),
                "old": "value = 0",
                "new": "value = 1",
                "expected_revision": hashlib.sha256(b"value = 0\n").hexdigest(),
            },
        )
        self.assertEqual(result.status, "completed", result.error)
        self.assertEqual(target.read_text(), "value = 1\n")
        self.assertEqual(
            result.output["source_revision"], hashlib.sha256(b"value = 1\n").hexdigest()
        )
        (record,) = self.broker.records(self.authorization.scope)
        self.assertEqual(record.request.action.name, "replace_file")
        self.assertEqual(record.decision.decided_by, "protoagent-auto")

    async def test_auto_mode_does_not_expand_planned_file_scope(self):
        self.attempt.record.allowed_paths = (str(self.root / "allowed.py"),)
        target = self.root / "forbidden.py"
        result = await self.auto_tool(
            self.coder, "create_file", {"path": str(target), "content": "no"}, mode="auto"
        )
        self.assertNotEqual(result.status, "completed")
        self.assertFalse(target.exists())
        self.assertEqual(self.broker.records(self.authorization.scope), ())

    async def test_edits_mode_still_waits_for_command_approval(self):
        self.bridge.bind(self.broker, self.authorization, approval_mode="edits")
        marker = self.root / "command-ran"
        handle = self.start_tool(
            self.verifier,
            "execute_command",
            {
                "argv": [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path('command-ran').touch()",
                ],
                "cwd": str(self.root),
                "env": {},
            },
        )
        controls = asyncio.create_task(self.bridge.serve(handle))
        try:
            async with asyncio.timeout(3):
                while not self.bridge.request_path.exists():
                    await asyncio.sleep(0.01)
            (record,) = await self.pending()
            self.assertFalse(marker.exists())
            self.broker.resolve(
                ApprovalDecision(False, record.request.request_id),
                scope=self.authorization.scope,
                fingerprint=record.fingerprint,
            )
            self.assertNotEqual((await handle.result()).status, "completed")
            self.assertFalse(marker.exists())
        finally:
            controls.cancel()
            with suppress(asyncio.CancelledError):
                await controls

    async def test_auto_commands_use_native_executor_and_receipts(self):
        result = await self.auto_tool(
            self.verifier,
            "execute_command",
            {
                "argv": [sys.executable, "-c", "print('actual execution')"],
                "cwd": str(self.root),
                "env": {},
            },
            mode="auto",
        )
        self.assertEqual(result.status, "completed", result.error)
        self.assertIn("actual execution", result.output["stdout"])
        self.assertTrue(any(event.type == "action.completed" for event in result.report.events))

    async def test_auto_approval_does_not_override_changed_preimage(self):
        target = self.root / "stale"
        target.write_text("original")
        handle = self.start_tool(
            self.coder, "replace_file", {"path": str(target), "content": "proposal"}
        )
        await self.pending()
        target.write_text("external edit")
        self.bridge.bind(self.broker, self.authorization, approval_mode="auto")
        controls = asyncio.create_task(self.bridge.serve(handle))
        try:
            result = await handle.result()
            self.assertNotEqual(result.status, "completed")
            self.assertEqual(target.read_text(), "external edit")
        finally:
            controls.cancel()
            with suppress(asyncio.CancelledError):
                await controls

    async def test_restore_still_requires_manual_approval_in_auto_mode(self):
        target = self.root / "restore"
        result = await self.auto_tool(
            self.coder, "create_file", {"path": str(target), "content": "applied"}, mode="auto"
        )
        handle = self.start_tool(
            self.coder, "restore_change", {"change_id": result.output["change_id"]}
        )
        controls = asyncio.create_task(self.bridge.serve(handle))
        try:
            async with asyncio.timeout(3):
                while not self.bridge.request_path.exists():
                    await asyncio.sleep(0.01)
            request = json.loads(self.bridge.request_path.read_text())
            self.assertEqual(request["action"]["name"], "restore_change")
            self.assertEqual(target.read_text(), "applied")
            (record,) = await self.pending()
            self.broker.resolve(
                ApprovalDecision(False, record.request.request_id),
                scope=self.authorization.scope,
                fingerprint=record.fingerprint,
            )
            await handle.result()
            self.assertEqual(target.read_text(), "applied")
        finally:
            controls.cancel()
            with suppress(asyncio.CancelledError):
                await controls
