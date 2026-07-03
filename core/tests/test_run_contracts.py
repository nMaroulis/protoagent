from __future__ import annotations

import unittest

from protoagent_core.run_contracts import infer_run_contract, validate_run_completion


class RunContractTests(unittest.TestCase):
    def test_write_prompt_requires_coder_or_write_artifact(self) -> None:
        contract = infer_run_contract("Implement the runtime guard and update the docs")

        self.assertEqual(contract.task_kind, "workspace-change")
        self.assertTrue(contract.requires_coder)
        self.assertTrue(contract.requires_write)
        self.assertIn("coder", contract.expected_workers)
        self.assertIn("diff_preview", contract.expected_artifacts)

    def test_read_only_prompt_does_not_require_write_artifact(self) -> None:
        contract = infer_run_contract("Explain how runtime cancellation works")

        self.assertEqual(contract.task_kind, "repository-question")
        self.assertFalse(contract.requires_coder)
        self.assertFalse(contract.requires_write)

    def test_write_contract_fails_without_coder_or_artifact(self) -> None:
        contract = infer_run_contract("Update the CLI docs")

        validation = validate_run_completion(
            contract,
            answer="I described the docs update.",
            status="completed",
            run_events=[],
            approval_requests=[],
            diff_items=[],
        )

        self.assertFalse(validation.satisfied)
        self.assertEqual(validation.outcome, "incomplete")
        self.assertIn("Coder worker", validation.missing[0])

    def test_write_contract_accepts_approval_request(self) -> None:
        contract = infer_run_contract("Update the CLI docs")

        validation = validate_run_completion(
            contract,
            answer="Prepared the docs update.",
            status="completed",
            run_events=[],
            approval_requests=[{"request_id": "approval-1"}],
            diff_items=[],
        )

        self.assertTrue(validation.satisfied)
        self.assertEqual(validation.outcome, "satisfied")

    def test_write_contract_accepts_explicit_blocker(self) -> None:
        contract = infer_run_contract("Create the missing integration file")

        validation = validate_run_completion(
            contract,
            answer="Blocked: no path was provided for the new file.",
            status="completed",
            run_events=[],
            approval_requests=[],
            diff_items=[],
        )

        self.assertTrue(validation.satisfied)
        self.assertEqual(validation.outcome, "blocked")


if __name__ == "__main__":
    unittest.main()
