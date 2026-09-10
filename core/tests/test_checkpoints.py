"""Verify exact-byte restoration and approval-time conflict handling."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from protolink import ActionDeniedError, RunContext

from protoagent_core import checkpoints
from protoagent_core.agents.coder import create_coder_agent
from protoagent_core.verification import VerificationEvidence


class CheckpointTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "workspace"
        self.root.mkdir()
        self.config = patch("protoagent_core.config.CONFIG_DIR", Path(self.temp.name) / "state")
        self.config.start()
        self.evidence = VerificationEvidence()
        self.agent = create_coder_agent(
            workspace=str(self.root),
            transport="runtime",
            tool_only=True,
            approval_handler=lambda *_: True,
            evidence=self.evidence,
        )
        self.context = RunContext(workspace_uri=self.root.as_uri())

    def tearDown(self) -> None:
        self.config.stop()
        self.temp.cleanup()

    async def write(self, name: str, content: str):
        return await self.agent.call_tool_in_context(
            "generate_unified_diff", self.context, path=name, updated_content=content
        )

    async def test_undo_preserves_preexisting_dirty_bytes_and_other_files(self) -> None:
        target = self.root / "file.txt"
        target.write_bytes(b"user changes\r\n")
        target.chmod(0o755)
        other = self.root / "other.txt"
        other.write_text("unrelated")
        result = await self.write("file.txt", "agent change\n")
        restored = await self.agent.call_tool_in_context(
            "restore_checkpoint", self.context, checkpoint_id=result["checkpoint_id"]
        )
        self.assertTrue(restored["restored"])
        self.assertEqual(target.read_bytes(), b"user changes\r\n")
        self.assertEqual(target.stat().st_mode & 0o777, 0o755)
        self.assertEqual(other.read_text(), "unrelated")
        self.assertEqual(checkpoints.list_checkpoints(str(self.root)), [])
        self.assertEqual(self.evidence.revision(), 2)

    async def test_undo_new_file_removes_only_that_file(self) -> None:
        result = await self.agent.call_tool_in_context(
            "create_new_file", self.context, path="new.txt", content="new"
        )
        await self.agent.call_tool_in_context(
            "restore_checkpoint", self.context, checkpoint_id=result["checkpoint_id"]
        )
        self.assertFalse((self.root / "new.txt").exists())

    async def test_conflicting_user_edit_is_never_reverted(self) -> None:
        result = await self.write("file.txt", "agent")
        (self.root / "file.txt").write_text("new user edit")
        with self.assertRaisesRegex(ValueError, "Undo conflict"):
            await self.agent.call_tool_in_context(
                "restore_checkpoint", self.context, checkpoint_id=result["checkpoint_id"]
            )
        self.assertEqual((self.root / "file.txt").read_text(), "new user edit")

    async def test_file_changed_during_approval_requires_a_new_preview(self) -> None:
        target = self.root / "file.txt"
        target.write_text("original")

        async def approve_after_change(*_):
            target.write_text("user edit during approval")
            return True

        agent = create_coder_agent(
            workspace=str(self.root),
            transport="runtime",
            tool_only=True,
            approval_handler=approve_after_change,
        )
        with self.assertRaisesRegex(ValueError, "changed since approval"):
            await agent.call_tool_in_context(
                "generate_unified_diff", self.context, path="file.txt", updated_content="agent"
            )
        self.assertEqual(target.read_text(), "user edit during approval")
        self.assertEqual(checkpoints.list_checkpoints(str(self.root)), [])

    async def test_latest_is_fixed_before_approval_and_denial_does_not_mutate(self) -> None:
        result = await self.write("file.txt", "agent")

        async def deny(request, _):
            self.assertEqual(
                request.action.payload["arguments"]["checkpoint_id"], result["checkpoint_id"]
            )
            return False

        agent = create_coder_agent(
            workspace=str(self.root), transport="runtime", tool_only=True, approval_handler=deny
        )
        with self.assertRaises(ActionDeniedError):
            await agent.call_tool_in_context("restore_checkpoint", self.context)
        self.assertEqual((self.root / "file.txt").read_text(), "agent")
        self.assertEqual(len(checkpoints.list_checkpoints(str(self.root))), 1)

    async def test_snapshots_are_project_scoped(self) -> None:
        result = await self.write("file.txt", "agent")
        other = Path(self.temp.name) / "other"
        other.mkdir()
        with self.assertRaises(ValueError):
            checkpoints.restore_preview(result["checkpoint_id"], str(other))

    async def test_approval_freezes_resolved_target_when_a_symlink_is_repointed(self) -> None:
        first = self.root / "first.py"
        second = self.root / "second.py"
        first.write_text("old", encoding="utf-8")
        second.write_text("old", encoding="utf-8")
        alias = self.root / "alias.py"
        alias.symlink_to(first)

        def approve(*_):
            alias.unlink()
            alias.symlink_to(second)
            return True

        agent = create_coder_agent(
            workspace=str(self.root), transport="runtime", tool_only=True, approval_handler=approve
        )
        await agent.call_tool_in_context(
            "generate_unified_diff", RunContext(), path="alias.py", updated_content="new"
        )
        self.assertEqual(first.read_text(), "new")
        self.assertEqual(second.read_text(), "old")

    async def test_parallel_writes_keep_independent_expected_hashes(self) -> None:
        results = await asyncio.gather(self.write("one.txt", "one"), self.write("two.txt", "two"))
        self.assertNotEqual(results[0]["checkpoint_id"], results[1]["checkpoint_id"])
        self.assertEqual(len(checkpoints.list_checkpoints(str(self.root))), 2)
