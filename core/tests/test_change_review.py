"""Final diffs report native effects, including partial runs and dirty sources."""

from protolink import RunReport
from runtime_support import NativeRuntimeCase

from protoagent_core.change_review import review_changes


class ChangeReviewTests(NativeRuntimeCase):
    def review(self, *results):
        report = RunReport.from_events(
            [event for result in results for event in result.report.events]
        )
        return review_changes(self.attempt, report)

    async def test_incremental_edits_combine_and_denied_proposals_are_excluded(self):
        path = self.root / "source.py"
        path.write_text("user pre-existing work\nvalue = 0\n")
        one = await self.tool(
            self.coder,
            "replace_file",
            {"path": str(path), "content": "user pre-existing work\nvalue = 1\n"},
        )
        two = await self.tool(
            self.coder,
            "replace_file",
            {"path": str(path), "content": "user pre-existing work\nvalue = 2\n"},
        )
        denied = await self.tool(
            self.coder,
            "replace_file",
            {"path": str(path), "content": "denied proposal\n"},
            approved=False,
        )
        review, diffs = self.review(one, two, denied)
        self.assertEqual(review["edit_count"], 2)
        self.assertEqual(review["file_count"], 1)
        self.assertEqual(review["additions"], 1)
        self.assertEqual(review["removals"], 1)
        self.assertIn("-value = 0", diffs[0]["diff"])
        self.assertIn("+value = 2", diffs[0]["diff"])
        self.assertNotIn("value = 1", diffs[0]["diff"])
        self.assertNotIn("denied proposal", diffs[0]["diff"])
        self.assertFalse(review["files"][0]["stale"])

    async def test_external_edits_are_not_attributed_to_agent(self):
        path = self.root / "source"
        path.write_text("original\n")
        one = await self.tool(
            self.coder, "replace_file", {"path": str(path), "content": "agent one\n"}
        )
        path.write_text("external intervening work\n")
        two = await self.tool(
            self.coder,
            "replace_file",
            {"path": str(path), "content": "external intervening work\nagent two\n"},
        )
        review, diffs = self.review(one, two)
        self.assertIn("interrupted", review["files"][0]["note"])
        self.assertNotIn("+external intervening work", diffs[0]["diff"])
        path.write_text("later outside work\n")
        review, _ = self.review(one, two)
        self.assertTrue(review["files"][0]["stale"])

    async def test_restoration_leaves_no_net_diff(self):
        path = self.root / "source"
        path.write_text("original\n")
        changed = await self.tool(
            self.coder, "replace_file", {"path": str(path), "content": "agent edit\n"}
        )
        restored = await self.tool(
            self.coder, "restore_change", {"change_id": changed.output["change_id"]}
        )
        review, diffs = self.review(changed, restored)
        self.assertEqual(review["files"][0]["kind"], "unchanged")
        self.assertEqual(review["file_count"], 0)
        self.assertEqual(diffs, [])

    async def test_create_and_missing_final_newline_have_valid_diff(self):
        created = await self.tool(
            self.coder, "create_file", {"path": str(self.root / "new"), "content": "new file"}
        )
        review, diffs = self.review(created)
        self.assertEqual(review["files"][0]["kind"], "created")
        self.assertIn("--- /dev/null\n", diffs[0]["diff"])
        self.assertIn("\\ No newline at end of file\n", diffs[0]["diff"])

    async def test_binary_effect_has_summary_without_text_diff(self):
        changed = await self.tool(
            self.coder,
            "create_file",
            {"path": str(self.root / "binary"), "content": "contains\0nul"},
        )
        review, diffs = self.review(changed)
        self.assertEqual(review["file_count"], 1)
        self.assertIn("Binary", review["summary"])
        self.assertEqual(diffs, [])

    async def test_oversized_review_does_not_claim_an_unchecked_revision_is_stale(self):
        from unittest.mock import patch

        # Lower only the application's display cap, retaining native execution.
        changed = await self.tool(
            self.coder,
            "create_file",
            {"path": str(self.root / "large"), "content": "large file contents\n"},
        )
        with patch("protoagent_core.change_review.MAX_FILE_BYTES", 8):
            review, diffs = self.review(changed)
        self.assertEqual(review["file_count"], 1)
        self.assertIsNone(review["files"][0]["stale"])
        self.assertIn("could not be checked", review["summary"])
        self.assertIn("review limit", review["summary"])
        self.assertEqual(diffs, [])
