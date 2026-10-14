"""Native offline evaluations retain engine evidence and avoid live providers."""

import unittest
from unittest.mock import patch

from protoagent_core.harness_eval import run_harness_eval


class HarnessEvaluationTests(unittest.TestCase):
    def test_offline_cases_have_fresh_factories_and_linked_receipts(self):
        def offline_kwargs(provider, model):
            if provider != "mock":
                raise AssertionError("live provider")
            return {"model": model}

        with patch(
            "protoagent_core.llm.llm_kwargs",
            side_effect=offline_kwargs,
        ):
            report = run_harness_eval(repetitions=2)
        self.assertTrue(report["passed"], report["samples"])
        self.assertEqual(len(report["samples"]), 4)
        self.assertEqual(report["scores"]["linked_child_receipt"], 1)
        self.assertEqual(report["engine"], "ProtoLink evaluate")
