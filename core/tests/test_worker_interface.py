"""Simple calls still execute through native children, policy and receipts."""

import asyncio
import json
from unittest.mock import patch

from protolink import Agent, ApprovalDecision, CapabilityPolicy, RunHandle, SubagentLimits, Task
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
        with self.assertRaisesRegex(RuntimeError, "authorization"):
            await parent.tools["read_file"](path="README.md")

    def test_installation_rejects_empty_roster_and_collisions(self):
        with self.assertRaisesRegex(ValueError, "owned local children"):
            install_worker_interface(Agent(name="empty", verbosity=0))
        parent = self.parent([])
        with self.assertRaisesRegex(ValueError, "collides"):
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
