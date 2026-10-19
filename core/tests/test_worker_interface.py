"""Simple calls still execute through native children, policy and receipts."""

import asyncio
import hashlib
import json
from unittest.mock import patch

from protolink import Agent, ApprovalDecision, CapabilityPolicy, RunHandle, SubagentLimits, Task
from protolink.llms.base import MAX_INFER_STEPS
from protolink.llms.factory import create_llm
from runtime_support import NativeRuntimeCase

from protoagent_core.agents.architect import create_architect_agent
from protoagent_core.agents.coder import create_coder_agent
from protoagent_core.agents.deck import create_agent_deck
from protoagent_core.agents.explorer import create_explorer_agent
from protoagent_core.request_budget import configure_agent_context
from protoagent_core.run_contracts import infer_run_contract
from protoagent_core.task_record import discover_checks
from protoagent_core.verification import validate_completion
from protoagent_core.worker_interface import install_worker_interface


class WorkerInterfaceTests(NativeRuntimeCase):
    async def test_editing_hint_does_not_schedule_baselines_or_affect_questions(self):
        (self.root / "sample.py").write_text("value = 0\n")
        record = self.attempt.record
        record.checks = discover_checks(str(self.root))
        record.selected = tuple(record.checks)
        for goal, should_code in (
            ("Fix sample.py", True),
            ("Explain this project", False),
            ("Update README.md", True),
        ):
            record.objective = goal
            seen = []
            parent = self.parent([{"type": "final", "content": "Fixture outcome"}], seen=seen)
            result = await self.start(parent, goal).result()
            self.assertEqual(result.status, "completed", result.error)
            self.assertNotIn("Before editing call run_check", seen[0][0]["content"])
            self.assertEqual(
                "The harness runs checks once afterwards" in seen[0][0]["content"], should_code
            )

    async def test_budget_expiry_while_approval_is_pending_has_a_clear_reason(self):
        from protoagent_core.runtime import _run_agent_deck

        (self.root / "test_sample.py").write_text("import unittest\n")
        response = {
            "type": "tool_call",
            "tool": "run_check",
            "args": {"check_id": "python-tests", "phase": "baseline"},
        }
        with (
            patch(
                "protoagent_core.agents.common.create_llm_from_config",
                side_effect=lambda *args: create_llm("mock", sequential_responses=[response]),
            ),
            patch.dict(
                "os.environ",
                {"PROTOAGENT_RUN_MAX_SECONDS": "0.5", "PROTOAGENT_AGENT_TRANSPORT": "local"},
            ),
        ):
            result = await _run_agent_deck(
                "Run the baseline",
                "mock",
                "offline",
                str(self.root),
                None,
                self.bridge,
                {"resolved": "small"},
                tester_enabled=False,
            )
        self.assertEqual(result["status"], "blocked", result)
        self.assertIn("waiting for your approval", result["answer"])
        self.assertIn("Viewing details does not approve", result["answer"])
        self.assertFalse(any(e["type"] == "process.started" for e in result["run_events"]))
        self.assertFalse(result["completion_validation"]["applied"])

    def test_small_owned_decks_install_only_enabled_worker_routes(self):
        with patch(
            "protoagent_core.agents.common.create_llm_from_config",
            side_effect=lambda *args: create_llm("mock"),
        ):
            for profile, local, expected in (
                ("small", True, True),
                ("small", False, False),
                ("medium", True, False),
                ("auto", True, True),
            ):
                deck = create_agent_deck(
                    provider="mock",
                    model="gemma4:e4b",
                    workspace=str(self.root),
                    transport=None,
                    prompt_profile=profile,
                    local_children=local,
                    tester_enabled=False,
                    scout_enabled=False,
                    mcp_enabled=False,
                    checkpoints=self.checkpoints,
                    approval_handler=self.broker,
                    authorization=self.authorization,
                    attempt=self.attempt,
                )
                interface = getattr(deck["architect"], "protoagent_worker_interface", None)
                self.assertEqual(interface is not None, expected)
                if interface:
                    self.assertIn("coder", interface)
                    self.assertNotIn("tester", interface)
                    self.assertNotIn("web_search", interface)
                    self.assertNotIn("call_mcp_tool", interface)

    def test_enabled_optional_workers_have_typed_routes_and_keep_their_roles(self):
        with patch(
            "protoagent_core.agents.common.create_llm_from_config",
            side_effect=lambda *args: create_llm("mock"),
        ):
            deck = create_agent_deck(
                provider="mock",
                model="offline",
                workspace=str(self.root),
                transport=None,
                prompt_profile="small",
                local_children=True,
                tester_enabled=True,
                scout_enabled=True,
                mcp_enabled=True,
                mcp_servers={},
                checkpoints=self.checkpoints,
                approval_handler=self.broker,
                authorization=self.authorization,
                attempt=self.attempt,
            )
        routes = deck["architect"].protoagent_worker_interface
        self.assertEqual(routes["tester"]["action"], "infer")
        for name in (
            "web_search",
            "fetch_url",
            "list_mcp_tools",
            "mcp_tool_schema",
            "call_mcp_tool",
        ):
            self.assertEqual(routes[name]["action"], "tool_call")
        self.assertIsNone(deck["scout"].llm)
        self.assertIsNone(deck["mcp"].llm)
        self.assertIsNone(deck["verifier"].llm)

    def parent(self, responses, *, children=None, seen=None):
        record = self.attempt.record
        model = create_llm("mock", sequential_responses=responses)
        if seen is not None:
            acquire = model.call

            def observe(history):
                seen.append(history.to_list())
                return acquire(history)

            model.call = observe
        explorer = create_explorer_agent(
            workspace=str(self.root),
            provider="mock",
            model="offline",
            transport=None,
            record=record,
            prompt_profile="small",
        )
        with patch("protoagent_core.agents.architect.create_selected_llm", return_value=model):
            parent = create_architect_agent(
                workspace=str(self.root),
                provider="mock",
                model="offline",
                transport=None,
                record=record,
                subagents=children or [explorer, self.verifier],
                tester_enabled=False,
            )
        install_worker_interface(parent)
        configure_agent_context(
            parent,
            record,
            compact_protocol=True,
            fallback_window=8192,
            cards=[child.card for child in parent.subagents.values()],
        )
        return parent

    def start(self, parent, prompt):
        task = Task.create_infer(prompt)
        self.context.attach_to_task(task)
        handle = RunHandle.start(parent, task)
        self.handles.append(handle)
        return handle

    async def test_read_is_a_native_explorer_child_and_prompt_omits_nested_protocol(self):
        (self.root / "README.md").write_text("An application for measuring widgets.\n")
        seen = []
        parent = self.parent(
            [
                {"type": "tool_call", "tool": "read_file", "args": {"path": "README.md"}},
                {"type": "final", "content": "This application measures widgets."},
            ],
            seen=seen,
        )
        result = await self.start(parent, "What's this project about?").result()
        self.assertEqual(result.status, "completed", result.error)
        system = seen[0][0]["content"]
        self.assertNotIn("args_json", system)
        self.assertNotIn('"type":"agent_call"', system)
        self.assertNotIn("Your tools: " + "worker_packet", system)
        self.assertNotIn(
            "worker_packet",
            json.dumps(json.loads(system.split("Your tools: ")[1].split("\n\n")[0])),
        )
        self.assertTrue(
            any(
                e.agent_name == "explorer"
                and e.type == "action.completed"
                and e.payload.get("action", {}).get("name") == "read_file"
                for e in result.report.events
            )
        )
        self.assertEqual(len(result.task.metadata["subagent_runs"]), 1)
        self.assertFalse(self.broker.records(self.authorization.scope))

    async def test_direct_verifier_call_retains_exact_native_approval(self):
        (self.root / "test_sample.py").write_text(
            "import unittest\nclass Regression(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(42, 42)\n"
        )
        record = self.attempt.record
        record.checks = discover_checks(str(self.root))
        record.selected = tuple(record.checks)
        parent = self.parent(
            [
                {
                    "type": "tool_call",
                    "tool": "run_check",
                    "args": {"check_id": "python-tests", "phase": "baseline"},
                },
                {"type": "final", "content": "Baseline executed."},
            ]
        )
        handle = self.start(parent, "Run the existing baseline")
        (approval,) = await self.pending()
        self.assertEqual(approval.request.action.name, "execute_command")
        self.assertEqual(
            approval.request.action.payload["arguments"]["argv"],
            list(record.checks["python-tests"].argv),
        )
        self.assertEqual(approval.request.action.metadata["check_phase"], "baseline")
        self.broker.resolve(
            ApprovalDecision(True, approval.request.request_id),
            scope=self.authorization.scope,
            fingerprint=approval.fingerprint,
        )
        result = await handle.result()
        self.assertEqual(result.status, "completed", result.error)

        self.assertEqual(len(self.broker.records(self.authorization.scope)), 1)
        self.assertFalse(self.attempt.checking)
        self.assertTrue(
            any(
                e.agent_name == "verifier" and e.type == "process.finished"
                for e in result.report.events
            )
        )

    async def test_invalid_command_preparation_is_corrected_before_effect_or_approval(self):
        (self.root / "README.md").write_text("Use the test folder.\n")
        seen = []
        parent = self.parent(
            [
                {
                    "type": "tool_call",
                    "tool": "execute_command",
                    "args": {"argv": ["protoagent-missing-fixture-program"]},
                },
                {"type": "tool_call", "tool": "read_file", "args": {"path": "README.md"}},
                {
                    "type": "tool_call",
                    "tool": "execute_command",
                    "args": {"argv": ["mkdir", "test"], "cwd": ".", "env": {}},
                },
                {"type": "final", "content": "Directory created."},
            ],
            seen=seen,
        )
        handle = self.start(parent, "Create a test folder")
        (approval,) = await self.pending()
        self.assertFalse((self.root / "test").exists())
        self.assertEqual(approval.request.action.payload["arguments"]["cwd"], str(self.root))
        self.broker.resolve(
            ApprovalDecision(True, approval.request.request_id),
            scope=self.authorization.scope,
            fingerprint=approval.fingerprint,
        )
        result = await handle.result()
        self.assertEqual(result.status, "completed", result.error)
        self.assertTrue((self.root / "test").is_dir())
        self.assertIn("invalid_command_preparation", json.dumps(seen[1]))
        self.assertEqual(len(result.task.metadata["subagent_runs"]), 3)
        self.assertEqual(len(self.broker.records(self.authorization.scope)), 1)
        self.assertTrue(any(event.type == "action.rejected" for event in result.report.events))
        self.assertFalse(any(event.type == "action.failed" for event in result.report.events))

    async def test_move_tests_to_package_keeps_frozen_runner_and_current_dependencies(self):
        source = self.root / "test_value.py"
        content = (
            "import unittest\nclass Regression(unittest.TestCase):\n"
            "    def test_value(self):\n        self.assertEqual(42, 42)\n"
        )
        source.write_text(content)
        record = self.attempt.record
        record.objective = "Move the tests into a test folder"
        record.checks = discover_checks(str(self.root))
        record.selected = tuple(record.checks)
        original_argv = record.checks["python-tests"].argv
        model = create_llm(
            "mock",
            sequential_responses=[
                {
                    "type": "tool_call",
                    "tool": "create_file",
                    "args": {"path": "test/__init__.py", "content": '"""Regression tests."""\n'},
                },
                {"type": "final", "content": "Added the unittest package marker."},
            ],
        )
        with patch("protoagent_core.agents.coder.create_selected_llm", return_value=model):
            coder = create_coder_agent(
                workspace=str(self.root),
                transport=None,
                approval_handler=self.broker,
                authorization=self.authorization,
                attempt=self.attempt,
                checkpoints=self.checkpoints,
                prompt_profile="small",
            )
        configure_agent_context(coder, record, compact_protocol=True)
        parent = self.parent(
            [
                {
                    "type": "tool_call",
                    "tool": "execute_command",
                    "args": {"argv": ["mkdir", "test"], "cwd": ".", "env": {}},
                },
                {
                    "type": "tool_call",
                    "tool": "execute_command",
                    "args": {
                        "argv": ["mv", "-n", "test_value.py", "test/test_value.py"],
                        "cwd": ".",
                        "env": {},
                    },
                },
                {
                    "type": "tool_call",
                    "tool": "coder",
                    "args": {
                        "task": "Add test/__init__.py to retain recursive unittest discovery."
                    },
                },
                {
                    "type": "tool_call",
                    "tool": "run_check",
                    "args": {"check_id": "python-tests", "phase": "verify"},
                },
                {
                    "type": "final",
                    "content": "Moved the tests and ran the frozen repository check.",
                },
            ],
            children=[coder, self.verifier],
        )
        handle = self.start(parent, record.objective)
        for expected in ("execute_command", "execute_command", "create_file", "execute_command"):
            (approval,) = await self.pending()
            self.assertEqual(approval.request.action.name, expected)
            self.broker.resolve(
                ApprovalDecision(True, approval.request.request_id),
                scope=self.authorization.scope,
                fingerprint=approval.fingerprint,
            )
        result = await handle.result()
        self.assertEqual(result.status, "completed", result.error)
        moved = self.root / "test/test_value.py"
        self.assertFalse(source.exists())
        self.assertEqual(moved.read_text(), content)
        self.assertEqual(record.checks["python-tests"].argv, original_argv)
        accepted = await validate_completion(
            infer_run_contract(record.objective),
            result.task,
            result.report,
            self.attempt,
            self.broker,
        )
        self.assertTrue(accepted.completion["satisfied"], accepted.completion)
        self.assertEqual(accepted.verification["status"], "passed")
        self.assertIn("Ran 1 test", accepted.verification["latest"][0]["stderr"])
        final_id = accepted.verification["latest"][0]["action_id"]
        self.assertIn(str(moved), {r.resource_id for r in self.attempt.command_revisions[final_id]})
        moved.write_text(content + "# Changed after verification\n")
        stale = await validate_completion(
            infer_run_contract(record.objective),
            result.task,
            result.report,
            self.attempt,
            self.broker,
        )
        self.assertEqual(stale.verification["status"], "stale")
        self.assertFalse(stale.completion["satisfied"])

    async def test_coder_call_runs_worker_then_final_check_verifies_actual_write(self):
        sample = self.root / "sample.py"
        sample.write_text("value = 0\n")
        (self.root / "test_sample.py").write_text(
            "import unittest\nfrom sample import value\nclass Regression(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(value, 42)\n"
        )
        record = self.attempt.record
        record.checks = discover_checks(str(self.root))
        record.selected = tuple(record.checks)
        record.plan(["sample.py"], ["Value is 42"])
        model = create_llm(
            "mock",
            sequential_responses=[
                {
                    "type": "tool_call",
                    "tool": "replace_file",
                    "args": {"path": str(sample), "content": "value = 42\n"},
                },
                {"type": "final", "content": "Applied the value fix."},
            ],
        )
        with patch("protoagent_core.agents.coder.create_selected_llm", return_value=model):
            coder = create_coder_agent(
                workspace=str(self.root),
                provider="mock",
                model="offline",
                transport=None,
                approval_handler=self.broker,
                authorization=self.authorization,
                attempt=self.attempt,
                checkpoints=self.checkpoints,
            )
        configure_agent_context(coder, record, compact_protocol=True)
        parent = self.parent(
            [
                {
                    "type": "tool_call",
                    "tool": "run_check",
                    "args": {"check_id": "python-tests", "phase": "baseline"},
                },
                {
                    "type": "tool_call",
                    "tool": "coder",
                    "args": {"task": "Set value in sample.py to 42."},
                },
                {
                    "type": "tool_call",
                    "tool": "run_check",
                    "args": {"check_id": "python-tests", "phase": "verify"},
                },
                {"type": "final", "content": "Applied and checked the fix."},
            ],
            children=[coder, self.verifier],
        )
        handle = self.start(parent, "Fix sample.py so value is 42")
        for expected in ("execute_command", "replace_file", "execute_command"):
            (approval,) = await self.pending()
            self.assertEqual(approval.request.action.name, expected)
            self.broker.resolve(
                ApprovalDecision(True, approval.request.request_id),
                scope=self.authorization.scope,
                fingerprint=approval.fingerprint,
            )
        result = await handle.result()
        self.assertEqual(result.status, "completed", result.error)
        accepted = await validate_completion(
            infer_run_contract("Fix sample.py"),
            result.task,
            result.report,
            self.attempt,
            self.broker,
        )
        self.assertTrue(accepted.completion["applied"], accepted.completion)
        self.assertTrue(accepted.completion["verified"], accepted.completion)
        self.assertEqual(len(result.task.metadata["subagent_runs"]), 3)

    @patch.dict("os.environ", {"PROTOAGENT_CODER_MAX_WINDOWS": "1"})
    async def test_partial_coder_handoff_can_finish_in_a_focused_new_child_without_replay(self):
        sample = self.root / "sample.py"
        sample.write_text("value = 0\nremaining = 0\n" + "# source\n" * MAX_INFER_STEPS)
        (self.root / "test_sample.py").write_text(
            "import unittest\nfrom sample import value, remaining\n"
            "class Regression(unittest.TestCase):\n"
            "    def test_values(self):\n"
            "        self.assertEqual((value, remaining), (42, 99))\n"
        )
        record = self.attempt.record
        record.checks = discover_checks(str(self.root))
        record.selected = tuple(record.checks)
        record.plan(["sample.py"], ["Both values are updated"])
        responses = [
            {
                "type": "tool_call",
                "tool": "replace_file",
                "args": {
                    "path": "sample.py",
                    "content": "value = 42\nremaining = 0\n" + "# source\n" * MAX_INFER_STEPS,
                },
            },
            *[
                {
                    "type": "tool_call",
                    "tool": "read_file",
                    "args": {"path": "sample.py", "start_line": line, "end_line": line},
                }
                for line in range(2, MAX_INFER_STEPS)
            ],
            {
                "type": "tool_call",
                "tool": "edit_file",
                "args": {
                    "path": "sample.py",
                    "old": "remaining = 0",
                    "new": "remaining = 99",
                    "expected_revision": hashlib.sha256(
                        sample.read_bytes().replace(b"value = 0", b"value = 42")
                    ).hexdigest(),
                },
            },
            {"type": "final", "content": "Finished the remaining edit; Architect should verify."},
        ]
        model = create_llm("mock", sequential_responses=responses)
        with patch("protoagent_core.agents.coder.create_selected_llm", return_value=model):
            coder = create_coder_agent(
                workspace=str(self.root),
                transport=None,
                prompt_profile="small",
                approval_handler=self.broker,
                authorization=self.authorization,
                attempt=self.attempt,
                checkpoints=self.checkpoints,
            )
        configure_agent_context(coder, record, compact_protocol=True)
        seen = []
        parent = self.parent(
            [
                {
                    "type": "tool_call",
                    "tool": "coder",
                    "args": {"task": "Update both values in sample.py."},
                },
                {
                    "type": "tool_call",
                    "tool": "run_check",
                    "args": {"check_id": "python-tests", "phase": "verify"},
                },
                {
                    "type": "tool_call",
                    "tool": "coder",
                    "args": {
                        "task": "Value is already applied. Update only remaining to 99 in sample.py."
                    },
                },
                {
                    "type": "tool_call",
                    "tool": "run_check",
                    "args": {"check_id": "python-tests", "phase": "verify"},
                },
                {"type": "final", "content": "Both changes applied and checked."},
            ],
            children=[coder, self.verifier],
            seen=seen,
        )
        handle = self.start(parent, "Fix both values in sample.py")
        for expected in ("replace_file", "replace_file", "execute_command"):
            (approval,) = await self.pending()
            self.assertEqual(approval.request.action.name, expected)
            self.broker.resolve(
                ApprovalDecision(True, approval.request.request_id),
                scope=self.authorization.scope,
                fingerprint=approval.fingerprint,
            )
        result = await handle.result()
        self.assertEqual(result.status, "completed", result.error)
        self.assertIn("worker_turn_limit", json.dumps(seen[1]))
        self.assertIn("coder_handoff_unresolved", json.dumps(seen[2]))
        self.assertEqual(record.reports["coder"]["status"], "done")
        applied = [
            event
            for event in result.report.events
            if event.type == "action.completed"
            and isinstance(event.payload.get("result"), dict)
            and event.payload.get("result", {}).get("state") == "applied"
        ]
        self.assertEqual(len(applied), 2, "Continuation must not replay the first edit")
        accepted = await validate_completion(
            infer_run_contract("Fix sample.py"),
            result.task,
            result.report,
            self.attempt,
            self.broker,
        )
        self.assertTrue(accepted.completion["satisfied"], accepted.completion)
        self.assertEqual(len(result.task.metadata["subagent_runs"]), 4)

    async def test_denied_command_stops_without_retry_or_execution(self):
        (self.root / "sample.py").write_text("value = 0\n")
        record = self.attempt.record
        record.checks = discover_checks(str(self.root))
        record.selected = tuple(record.checks)
        parent = self.parent(
            [
                {
                    "type": "tool_call",
                    "tool": "run_check",
                    "args": {"check_id": "python-tests", "phase": "baseline"},
                },
                {"type": "final", "content": "Must not continue."},
            ]
        )
        handle = self.start(parent, "Run the baseline")
        (approval,) = await self.pending()
        self.broker.resolve(
            ApprovalDecision(False, approval.request.request_id),
            scope=self.authorization.scope,
            fingerprint=approval.fingerprint,
        )
        result = await handle.result()
        self.assertEqual(result.status, "failed")
        self.assertFalse(any(e.type == "process.started" for e in result.report.events))
        self.assertEqual(len(self.broker.records(self.authorization.scope)), 1)

    async def test_cancellation_drains_child_waiting_for_approval(self):
        (self.root / "sample.py").write_text("value = 0\n")
        record = self.attempt.record
        record.checks = discover_checks(str(self.root))
        record.selected = tuple(record.checks)
        parent = self.parent(
            [
                {
                    "type": "tool_call",
                    "tool": "run_check",
                    "args": {"check_id": "python-tests", "phase": "baseline"},
                },
            ]
        )
        handle = self.start(parent, "Run the baseline")
        await self.pending()
        await handle.cancel("User canceled")
        result = await asyncio.wait_for(handle.result(), 3)
        self.assertEqual(result.status, "canceled")
        self.assertFalse(any(e.type == "process.started" for e in result.report.events))
        self.assertFalse(self.broker.pending(self.authorization.scope))

    async def test_disabled_workers_and_unregistered_aliases_are_not_invented(self):
        parent = self.parent(
            [
                {"type": "tool_call", "tool": "explorer:read_file", "args": {"path": "README.md"}},
                {"type": "final", "content": "Use an advertised call."},
            ]
        )
        self.assertNotIn("tester", parent.protoagent_worker_interface)
        self.assertNotIn("web_search", parent.tools)
        self.assertNotIn("call_mcp_tool", parent.tools)
        result = await self.start(parent, "Inspect the project").result()
        self.assertEqual(result.status, "completed", result.error)
        self.assertFalse(result.task.metadata.get("subagent_runs"))
        self.assertTrue(any(e.type == "action.rejected" for e in result.report.events))

    async def test_prepared_call_cannot_execute_without_a_native_supervisor(self):
        parent = self.parent([])
        with self.assertRaisesRegex(ValueError, "native parent tool declaration"):
            await parent.tools["read_file"](path="README.md")

    def test_installation_rejects_empty_roster_and_collisions(self):
        with self.assertRaisesRegex(ValueError, "owned local children"):
            install_worker_interface(Agent(name="empty", verbosity=0))
        parent = self.parent([])
        with self.assertRaisesRegex(ValueError, "conflicts"):
            install_worker_interface(parent)

    async def test_inherited_read_denial_is_not_bypassed_by_the_facade(self):
        (self.root / "README.md").write_text("Private source\n")
        parent = self.parent(
            [
                {"type": "tool_call", "tool": "read_file", "args": {"path": "README.md"}},
            ]
        )
        parent.action_authorizer.policy = CapabilityPolicy(
            {"agent.delegate": "allow", "workspace.read": "deny"}
        )
        result = await self.start(parent, "Read the project").result()
        self.assertEqual(result.status, "failed")
        self.assertFalse(
            any(
                e.agent_name == "explorer" and e.type == "action.completed"
                for e in result.report.events
            )
        )

    async def test_native_child_limit_stops_additional_dispatch(self):
        (self.root / "README.md").write_text("One\n")
        (self.root / "sample.py").write_text("value = 42\n")
        parent = self.parent(
            [
                {"type": "tool_call", "tool": "read_file", "args": {"path": "README.md"}},
                {"type": "tool_call", "tool": "read_file", "args": {"path": "sample.py"}},
            ]
        )
        parent.subagent_limits = SubagentLimits(max_children=1, max_concurrency=1)
        result = await self.start(parent, "Read the project").result()
        self.assertEqual(result.status, "failed")
        self.assertEqual(len(result.task.metadata["subagent_runs"]), 1)
