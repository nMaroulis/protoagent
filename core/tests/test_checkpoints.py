"""Native recovery with the application's storage, scope and approval policy."""

import sqlite3
from contextlib import closing

from protolink import ApprovalDecision
from runtime_support import NativeRuntimeCase

from protoagent_core.checkpoints import (
    legacy_checkpoints,
    list_checkpoints,
    select_change,
    workspace_key,
    workspace_writer,
)


class CheckpointTests(NativeRuntimeCase):
    async def test_restore_preserves_dirty_bytes_modes_and_other_files(self):
        target = self.root / "file.txt"
        target.write_bytes(b"user changes\r\n")
        target.chmod(0o755)
        (self.root / "other.txt").write_text("unrelated")
        result = await self.tool(
            self.coder, "replace_file", {"path": str(target), "content": "agent\n"}
        )
        self.assertEqual(result.status, "completed")
        change_id = result.output["change_id"]
        restored = await self.tool(self.coder, "restore_change", {"change_id": change_id})
        self.assertEqual(restored.output["state"], "restored")
        self.assertEqual(target.read_bytes(), b"user changes\r\n")
        self.assertEqual(target.stat().st_mode & 0o777, 0o755)
        self.assertEqual((self.root / "other.txt").read_text(), "unrelated")
        self.assertEqual(list_checkpoints(str(self.root))[0]["state"], "restored")
        self.assertEqual(len(self.broker.records(self.authorization.scope)), 2)

    async def test_create_preview_and_separately_denied_restore(self):
        target = self.root / "new.txt"
        result = await self.tool(self.coder, "create_file", {"path": str(target), "content": "new"})
        selected = select_change(self.checkpoints, "latest", str(self.root))
        self.assertEqual(selected.change_id, result.output["change_id"])
        preview = await self.start_tool(
            self.coder, "preview_change", {"change_id": selected.change_id}
        ).result()
        self.assertEqual(preview.status, "completed")
        denied = await self.tool(
            self.coder, "restore_change", {"change_id": selected.change_id}, approved=False
        )
        self.assertEqual(denied.status, "failed")
        self.assertTrue(target.exists())
        restored = await self.tool(self.coder, "restore_change", {"change_id": selected.change_id})
        self.assertEqual(restored.status, "completed")
        self.assertFalse(target.exists())

    async def test_stale_preimage_is_rejected_after_approval(self):
        target = self.root / "file.txt"
        target.write_text("original")
        handle = self.start_tool(
            self.coder, "replace_file", {"path": str(target), "content": "agent"}
        )
        (record,) = await self.pending()
        target.write_text("new user edit")
        self.broker.resolve(
            ApprovalDecision(True, record.request.request_id),
            scope=self.authorization.scope,
            fingerprint=record.fingerprint,
        )
        result = await handle.result()
        self.assertEqual(result.status, "failed")
        self.assertEqual(target.read_text(), "new user edit")
        self.assertFalse(any(event.type == "resource.changed" for event in result.report.events))

    async def test_restore_conflict_never_overwrites_newer_content(self):
        target = self.root / "file.txt"
        result = await self.tool(
            self.coder, "create_file", {"path": str(target), "content": "agent"}
        )
        target.write_text("user's newer version")
        restored = await self.start_tool(
            self.coder, "restore_change", {"change_id": result.output["change_id"]}
        ).result()
        self.assertEqual(restored.status, "failed")
        self.assertEqual(target.read_text(), "user's newer version")
        self.assertEqual(len(self.broker.records(self.authorization.scope)), 1)

    async def test_symlink_and_missing_parent_fail_before_approval(self):
        (self.root / "link").symlink_to(self.root / "elsewhere")
        for target in (self.root / "link", self.root / "missing" / "new.txt"):
            result = await self.start_tool(
                self.coder, "create_file", {"path": str(target), "content": "bad"}
            ).result()
            self.assertEqual(result.status, "failed")
        self.assertEqual(self.broker.records(self.authorization.scope), ())

    def test_private_storage_and_exclusive_namespace(self):
        path = self.config_dir / "recovery" / f"{workspace_key(str(self.root))}.sqlite"
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        with workspace_writer(str(self.root)):
            with self.assertRaisesRegex(RuntimeError, "Another ProtoAgent"):
                with workspace_writer(str(self.root)):
                    self.fail("second writer entered")

    def test_legacy_database_is_preserved_and_not_silently_imported(self):
        directory = self.config_dir / "checkpoints"
        directory.mkdir()
        path = directory / f"{workspace_key(str(self.root))}.sqlite"
        with closing(sqlite3.connect(path)) as db:
            db.execute(
                "CREATE TABLE checkpoints(id TEXT,path TEXT,created_at REAL,undone INTEGER,before BLOB)"
            )
            db.execute("INSERT INTO checkpoints VALUES ('old','file.txt',1,0,?)", (b"original",))
            db.commit()
        before = path.read_bytes()
        rows = legacy_checkpoints(str(self.root))
        self.assertEqual(rows[0]["state"], "legacy")
        self.assertNotIn("before", rows[0])
        with self.assertRaisesRegex(ValueError, "Legacy v0.2.1"):
            select_change(self.checkpoints, "latest", str(self.root))
        self.assertEqual(path.read_bytes(), before)

    async def test_revision_identity_conservatively_blocks_chained_undo(self):
        target = self.root / "file.txt"
        first = await self.tool(
            self.coder, "create_file", {"path": str(target), "content": "first"}
        )
        second = await self.tool(
            self.coder, "replace_file", {"path": str(target), "content": "second"}
        )
        await self.tool(self.coder, "restore_change", {"change_id": second.output["change_id"]})
        older = await self.start_tool(
            self.coder, "restore_change", {"change_id": first.output["change_id"]}
        ).result()
        self.assertEqual(older.status, "failed")
        self.assertEqual(target.read_text(), "first")
