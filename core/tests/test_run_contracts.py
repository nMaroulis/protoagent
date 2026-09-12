from __future__ import annotations

import unittest

from protoagent_core.run_contracts import infer_run_contract


class RunContractTests(unittest.TestCase):
    def test_write_prompt_requires_coder_or_write_artifact(self) -> None:
        contract = infer_run_contract("Implement the runtime guard and update the docs")

        self.assertEqual(contract.task_kind, "workspace-change")
        self.assertTrue(contract.requires_coder)
        self.assertTrue(contract.requires_write)
        self.assertIn("coder", contract.expected_workers)
        self.assertIn("executed_file_change", contract.expected_artifacts)

    def test_read_only_prompt_does_not_require_write_artifact(self) -> None:
        contract = infer_run_contract("Explain how runtime cancellation works")

        self.assertEqual(contract.task_kind, "repository-question")
        self.assertFalse(contract.requires_coder)
        self.assertFalse(contract.requires_write)

    def test_read_only_question_does_not_treat_symbol_verbs_as_write_intent(self) -> None:
        for prompt in (
            "Explain how create_agent_deck works",
            "What does update_metadata do?",
            "Review the tests and summarize their coverage",
            "How can I improve runtime performance?",
        ):
            with self.subTest(prompt=prompt):
                contract = infer_run_contract(prompt)
                self.assertEqual(contract.task_kind, "repository-question")
                self.assertFalse(contract.requires_write)

    def test_read_only_prefix_with_followup_edit_keeps_write_contract(self) -> None:
        for prompt in (
            "Explain how create_agent_deck works and update the docs",
            "Review this module. Fix the race condition.",
            "Review this module, fix the race condition.",
            "Find the bug; patch it.",
            "Review this module please fix the race condition.",
        ):
            with self.subTest(prompt=prompt):
                contract = infer_run_contract(prompt)
                self.assertEqual(contract.task_kind, "workspace-change")
                self.assertTrue(contract.requires_coder)

    def test_verification_only_requests_do_not_require_file_writes(self) -> None:
        for prompt in (
            "Run the tests",
            "Check test coverage",
            "Build the docs",
            "Please run the tests",
        ):
            with self.subTest(prompt=prompt):
                contract = infer_run_contract(prompt)
                self.assertFalse(contract.requires_write)
                self.assertEqual(contract.task_kind, "workspace-verification")
        self.assertTrue(infer_run_contract("Run the tests and fix failures").requires_write)
