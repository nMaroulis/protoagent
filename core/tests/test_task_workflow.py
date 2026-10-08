"""Behavioral regressions for task acceptance, small edits and baseline phases."""

import json
import sys
import unittest

from protolink import RunReport
from runtime_support import NativeRuntimeCase

from protoagent_core.coding_eval import EXERCISES, oracle_result, run_coding_eval
from protoagent_core.run_contracts import infer_run_contract
from protoagent_core.task_record import CheckSpec, TaskRecord
from protoagent_core.tools import read_file
from protoagent_core.verification import validate_completion


class TaskWorkflowTests(NativeRuntimeCase):
    def configure_check(self, code="assert open('sample.py').read() == 'value = 42\\n'"):
        record = self.attempt.record
        record.checks["regression"] = CheckSpec(
            "regression", (sys.executable, "-c", code), str(self.root), {}
        )
        record.selected = ("regression",)
        return record

    async def accept(self, *results, prompt="Fix sample.py"):
        return await validate_completion(
            infer_run_contract(prompt),
            results[-1].task,
            RunReport.from_events([event for result in results for event in result.report.events]),
            self.attempt,
            self.broker,
        )

    async def test_code_write_without_checks_is_incomplete(self):
        result = await self.tool(
            self.coder,
            "create_file",
            {"path": str(self.root / "sample.py"), "content": "value = 0\n"},
        )
        acceptance = await self.accept(result)
        self.assertFalse(acceptance.completion["satisfied"])
        self.assertTrue(acceptance.completion["applied"])
        self.assertFalse(acceptance.completion["verified"])

    async def test_code_request_cannot_complete_by_only_writing_documentation(self):
        result = await self.tool(
            self.coder,
            "create_file",
            {"path": str(self.root / "notes.md"), "content": "The bug should be fixed.\n"},
        )
        self.assertFalse(
            (await self.accept(result, prompt="Fix sample.py")).completion["satisfied"]
        )
        accepted = await self.accept(result, prompt="Write notes.md")
        self.assertTrue(accepted.completion["satisfied"])
        self.assertFalse(accepted.completion["verified"])

    async def test_unrelated_successful_command_does_not_verify_code(self):
        self.configure_check()
        written = await self.tool(
            self.coder,
            "create_file",
            {"path": str(self.root / "sample.py"), "content": "value = 0\n"},
        )
        command = await self.tool(
            self.verifier,
            "execute_command",
            {
                "argv": [sys.executable, "-c", "print('hello')"],
                "cwd": str(self.root),
                "env": {},
                "timeout_seconds": 3,
                "max_output_bytes": 1024,
            },
        )
        accepted = await self.accept(written, command)
        self.assertFalse(accepted.completion["satisfied"])
        self.assertEqual(accepted.verification["status"], "unverified")

    async def test_preparation_allows_edits_but_final_check_closes_phase(self):
        record = self.configure_check()
        prepared = await self.tool(
            self.verifier,
            "execute_command",
            {
                "argv": [sys.executable, "-c", "print('ready')"],
                "cwd": str(self.root),
                "env": {},
                "timeout_seconds": 3,
                "max_output_bytes": 1024,
            },
        )
        self.assertFalse(self.attempt.checking)
        written = await self.tool(
            self.coder,
            "create_file",
            {"path": str(self.root / "sample.py"), "content": "value = 42\n"},
        )
        checked = await self.tool(self.verifier, "run_check", {"check_id": "regression"})
        acceptance = await self.accept(prepared, written, checked)
        self.assertTrue(acceptance.completion["satisfied"])
        self.assertTrue(self.attempt.checking)
        self.assertTrue(record.frozen)
        denied = await self.start_tool(
            self.coder,
            "replace_file",
            {"path": str(self.root / "sample.py"), "content": "value = 0\n"},
        ).result()
        self.assertEqual(denied.status, "failed")

    async def test_failed_baseline_does_not_block_editing_or_count_as_final(self):
        self.configure_check("raise SystemExit(1)")
        baseline = await self.tool(
            self.verifier, "run_check", {"check_id": "regression", "phase": "baseline"}
        )
        self.assertFalse(self.attempt.checking)
        written = await self.tool(
            self.coder,
            "create_file",
            {"path": str(self.root / "sample.py"), "content": "value = 42\n"},
        )
        acceptance = await self.accept(baseline, written)
        self.assertFalse(acceptance.completion["satisfied"])
        self.assertFalse(acceptance.repairable)

    async def test_partial_check_plan_is_not_verified(self):
        record = self.configure_check()
        record.checks["second"] = CheckSpec(
            "second", (sys.executable, "-c", "pass"), str(self.root), {}
        )
        record.selected += ("second",)
        written = await self.tool(
            self.coder,
            "create_file",
            {"path": str(self.root / "sample.py"), "content": "value = 42\n"},
        )
        checked = await self.tool(self.verifier, "run_check", {"check_id": "regression"})
        acceptance = await self.accept(written, checked)
        self.assertFalse(acceptance.completion["satisfied"])
        self.assertFalse(acceptance.completion["verified"])

    async def test_empty_unittest_suite_is_not_successful_verification(self):
        self.configure_check("import sys; print('Ran 0 tests in 0.000s', file=sys.stderr)")
        written = await self.tool(
            self.coder,
            "create_file",
            {"path": str(self.root / "sample.py"), "content": "value = 42\n"},
        )
        checked = await self.tool(self.verifier, "run_check", {"check_id": "regression"})
        acceptance = await self.accept(written, checked)
        self.assertFalse(acceptance.completion["satisfied"])
        self.assertFalse(acceptance.repairable)
        self.assertTrue(acceptance.verification["latest"][0]["empty_test_suite"])

    async def test_explicit_read_only_instruction_denies_writes(self):
        self.attempt.record.forbids_write = True
        result = await self.start_tool(
            self.coder,
            "create_file",
            {"path": str(self.root / "sample.py"), "content": "value = 42\n"},
        ).result()
        self.assertEqual(result.status, "failed")
        self.assertFalse((self.root / "sample.py").exists())
        self.assertEqual(self.broker.records(self.authorization.scope), ())

    async def test_unresolved_worker_report_prevents_completion(self):
        self.attempt.record.report(
            "tester", "needs_context", "Need the public behavior", ["sample.py"]
        )
        result = await self.tool(
            self.coder, "create_file", {"path": str(self.root / "notes.md"), "content": "A note\n"}
        )
        acceptance = await self.accept(result, prompt="Write notes.md")
        self.assertFalse(acceptance.completion["satisfied"])
        self.attempt.record.report("tester", "done", "Resolved", [])
        self.assertTrue(
            (await self.accept(result, prompt="Write notes.md")).completion["satisfied"]
        )

    async def test_expanded_native_arguments_cannot_bypass_edit_preparation(self):
        path = self.root / "sample.py"
        path.write_text("value = 0\n")
        result = await self.start_tool(
            self.coder, "edit_file", {"path": str(path), "content": "value = 42\n"}
        ).result()
        self.assertEqual(result.status, "failed")
        self.assertEqual(path.read_text(), "value = 0\n")
        self.assertEqual(self.broker.records(self.authorization.scope), ())

    def test_polite_verification_and_negative_write_intent(self):
        contract = infer_run_contract("Can you run the tests?")
        self.assertFalse(contract.requires_write)
        self.assertEqual(contract.task_kind, "workspace-verification")
        read_only = infer_run_contract("Do not change anything; explain how to fix the bug")
        self.assertFalse(read_only.requires_write)
        self.assertTrue(read_only.forbids_write)
        combined = infer_run_contract(
            "Implement the proposal in the whitepaper, in the code and in the docs"
        )
        self.assertFalse(combined.allows_unverified_docs)
        checked_docs = infer_run_contract("Update README.md and run the tests")
        self.assertFalse(checked_docs.allows_unverified_docs)

    async def test_exact_edit_preserves_unrelated_source_and_rejects_old_revision(self):
        path = self.root / "sample.py"
        path.write_bytes(b"value = 0\r\nother = 9\r\n")
        source = read_file(str(path), str(self.root))
        result = await self.tool(
            self.coder,
            "edit_file",
            {
                "path": str(path),
                "old": "value = 0",
                "new": "value = 42",
                "expected_revision": source["revision"],
            },
        )
        self.assertEqual(result.status, "completed")
        self.assertEqual(path.read_bytes(), b"value = 42\r\nother = 9\r\n")
        stale = await self.start_tool(
            self.coder,
            "edit_file",
            {
                "path": str(path),
                "old": "other = 9",
                "new": "other = 0",
                "expected_revision": source["revision"],
            },
        ).result()
        self.assertEqual(stale.status, "failed")
        self.assertEqual(path.read_bytes(), b"value = 42\r\nother = 9\r\n")

    async def test_ambiguous_edit_has_no_effect(self):
        path = self.root / "sample.py"
        path.write_text("value = 0\nvalue = 0\n")
        source = read_file(str(path), str(self.root))
        result = await self.start_tool(
            self.coder,
            "edit_file",
            {
                "path": str(path),
                "old": "value = 0",
                "new": "value = 42",
                "expected_revision": source["revision"],
            },
        ).result()
        self.assertEqual(result.status, "failed")
        self.assertEqual(path.read_text(), "value = 0\nvalue = 0\n")
        self.assertEqual(self.broker.records(self.authorization.scope), ())

    def test_reads_are_paginated_and_do_not_duplicate_content(self):
        path = self.root / "large.py"
        path.write_text("".join(f"value_{i} = {i}\n" for i in range(2000)))
        source = read_file(str(path), str(self.root), start_line=101, end_line=105)
        self.assertIn("value_100", source["content"])
        self.assertNotIn("value_0 =", source["content"])
        self.assertNotIn("raw_content", source)
        self.assertEqual(source["next_line"], 106)

    def test_plan_cannot_be_weakened_after_effect(self):
        record = self.configure_check()
        record.plan(["sample.py"], ["Empty input returns zero"], ["regression"])
        record.frozen = True
        with self.assertRaisesRegex(ValueError, "frozen"):
            record.plan(["sample.py"], ["Anything is fine"], ["regression"])

    async def test_write_scope_still_allows_reading_supporting_source(self):
        record = self.configure_check()
        record.plan(["sample.py"], ["Fix the requested behavior"], ["regression"])
        supporting = self.root / "support.py"
        supporting.write_text("helper = 9\n")
        read = await self.start_tool(self.coder, "read_file", {"path": str(supporting)}).result()
        self.assertEqual(read.status, "completed")
        self.assertIn(str(supporting), record.source_paths)
        packet = record.packet("coder", "Inspect the helper", [str(supporting)])
        self.assertTrue(packet["sources"][0]["success"])
        self.assertEqual(packet["sources"][0]["content"], "helper = 9\n")
        self.assertEqual(
            self.coder.tools["read_file"].func(str(supporting))["content"], "helper = 9\n"
        )
        denied = await self.start_tool(
            self.coder, "replace_file", {"path": str(supporting), "content": "helper = 0\n"}
        ).result()
        self.assertEqual(denied.status, "failed")
        self.assertEqual(supporting.read_text(), "helper = 9\n")

    def test_packet_shares_a_total_source_budget_across_references(self):
        paths = []
        for index in range(8):
            path = self.root / f"source{index}.py"
            path.write_text("source line\n" * 1000)
            paths.append(str(path))
        packet = self.attempt.record.packet("tester", "Inspect regressions", paths)
        self.assertLessEqual(sum(len(source["content"]) for source in packet["sources"]), 6000)

    def test_project_check_manifest_rejects_symlink_configuration(self):
        folder = self.root / ".protoagent"
        folder.mkdir()
        target = self.root / "check-config.json"
        target.write_text('{"checks": []}')
        (folder / "project.json").symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            TaskRecord.create(str(self.root), "Fix sample.py")

    def test_discovery_reads_explicit_project_configuration(self):
        folder = self.root / ".protoagent"
        folder.mkdir()
        (folder / "project.json").write_text(
            json.dumps(
                {
                    "checks": [
                        {"id": "custom", "argv": [sys.executable, "-m", "unittest"], "env": {}}
                    ]
                }
            )
        )
        record = TaskRecord.create(str(self.root), "Fix code")
        self.assertEqual(record.selected, ("custom",))
        self.assertEqual(record.checks["custom"].source, ".protoagent/project.json")

    def test_independent_oracle_rejects_original_fixtures(self):
        for exercise in EXERCISES:
            for name, content in exercise["files"].items():
                (self.root / name).write_text(content)
            self.assertFalse(oracle_result(self.root, exercise["oracle"])["passed"])


class CodingEvaluationTests(unittest.TestCase):
    def evaluate(self, code):
        from pathlib import Path
        from unittest.mock import patch

        roots = []

        async def fake_run(*args, **kwargs):
            root = Path(args[3])
            roots.append(root)
            (root / "sample.py").write_text(code)
            return {"status": "completed", "verification": {"attempts": 1}}

        with (
            patch(
                "protoagent_core.config.load_config",
                return_value={
                    "active_provider": "ollama",
                    "providers": {"ollama": {"model": "mock"}},
                },
            ),
            patch("protoagent_core.runtime._run_agent_deck", side_effect=fake_run),
        ):
            result = run_coding_eval(mode="live", task_ids="empty-average", profiles="small")
        self.assertEqual(len(set(roots)), 2)
        self.assertTrue(all(not root.exists() for root in roots))
        self.assertIsInstance(result["summary"]["points"], int)
        self.assertIsInstance(result["profiles"][0]["points"], int)
        return result["profiles"][0]["tasks"]

    def test_claimed_completion_without_behavior_change_fails_independent_oracle(self):
        tasks = self.evaluate("def average(values):\n    return sum(values) / len(values)\n")
        self.assertEqual([task["architecture"] for task in tasks], ["deck", "single"])
        self.assertTrue(
            all(task["false_completion"] and task["score"]["score"] == 0 for task in tasks)
        )

    def test_real_behavior_change_passes_oracle_in_both_fresh_conditions(self):
        tasks = self.evaluate(
            "def average(values):\n    return sum(values) / len(values) if values else 0\n"
        )
        self.assertTrue(
            all(
                not task["oracle_before"]["passed"]
                and task["oracle_after"]["passed"]
                and task["score"]["score"] == 1
                for task in tasks
            )
        )
        self.assertTrue(all(task["repair_attempts"] == 0 for task in tasks))


class CompactProtocolTests(unittest.TestCase):
    def test_compact_prompt_preserves_input_schemas_and_drops_large_output_metadata(self):
        from protoagent_core.request_budget import compact_protocol_prompt

        schema = {
            "type": "object",
            "properties": {"path": {"type": "string", "maxLength": 64}},
            "required": ["path"],
        }
        tool = {
            "name": "read_file",
            "description": "Read source",
            "input_schema": schema,
            "output_schema": {"description": "LARGE_OUTPUT_METADATA " * 10000},
        }
        cards = "Agent 1:\n" + json.dumps(
            {"name": "explorer", "description": "Read evidence", "tools": [tool]}
        )
        prompt = compact_protocol_prompt(
            instructions="Inspect the source.",
            tools=json.dumps([tool]),
            cards=cards,
            name="architect",
            native=False,
        )
        self.assertNotIn("LARGE_OUTPUT_METADATA", prompt)
        self.assertIn('"action":"infer"', prompt)
        own = json.loads(prompt.split("Your tools: ", 1)[1].split("\n\n", 1)[0])
        workers = json.loads(prompt.split("Available workers: ", 1)[1])
        self.assertEqual(own[0]["input_schema"], schema)
        self.assertEqual(workers[0]["tools"][0]["input_schema"], schema)
        self.assertLess(len(prompt), 2048)

    def test_native_prompt_leaves_tool_syntax_to_provider(self):
        from protoagent_core.request_budget import compact_protocol_prompt

        prompt = compact_protocol_prompt(
            instructions="Read source.", tools="[]", cards="", name="coder", native=True
        )
        self.assertIn("provider's supplied tools", prompt)
        self.assertNotIn("Return one JSON", prompt)
        self.assertIn("Never delegate to yourself", prompt)
        self.assertNotIn("protolink_call_agent_tool", prompt)
