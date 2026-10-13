"""Native correction preserves observations, authority and acceptance boundaries."""

import hashlib
import json
from unittest.mock import patch

from protolink import Agent, ApprovalDecision, CapabilityPolicy, RunHandle, SubagentLimits, Task
from protolink.llms.factory import create_llm
from runtime_support import NativeRuntimeCase

from protoagent_core.agents.architect import create_architect_agent
from protoagent_core.agents.coder import create_coder_agent
from protoagent_core.agents.explorer import create_explorer_agent
from protoagent_core.request_budget import configure_agent_context
from protoagent_core.run_contracts import infer_run_contract
from protoagent_core.task_record import add_task_tools, discover_checks
from protoagent_core.verification import validate_completion


class EngineCorrectionTests(NativeRuntimeCase):
    async def test_prepared_packet_then_coder_dispatch_applies_and_verifies(self):
        sample = self.root / "sample.py"
        sample.write_text("value = 0\n")
        record = self.attempt.record
        record.checks = discover_checks(str(self.root))
        record.selected = tuple(record.checks)
        record.plan(["sample.py", "test_sample.py"], ["Value is 42"])
        worker = create_llm(
            "mock",
            sequential_responses=[
                {
                    "type": "tool_call",
                    "tool": "replace_file",
                    "args": {"path": str(sample), "content": "value = 42\n"},
                },
                {
                    "type": "tool_call",
                    "tool": "create_file",
                    "args": {
                        "path": str(self.root / "test_sample.py"),
                        "content": "import unittest\nfrom sample import value\nclass Regression(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(value, 42)\n",
                    },
                },
                {"type": "final", "content": "Applied value fix and its regression."},
            ],
        )
        with patch("protoagent_core.agents.coder.create_selected_llm", return_value=worker):
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
        prepared = []
        model = create_llm(
            "mock",
            sequential_responses=[
                {
                    "type": "agent_call",
                    "agent": "verifier",
                    "action": "tool_call",
                    "tool": "run_check",
                    "args": {"check_id": "python-tests", "phase": "baseline"},
                },
                {
                    "type": "tool_call",
                    "tool": "worker_packet",
                    "args": {
                        "role": "coder",
                        "objective": "Set value to 42 and add a regression",
                        "paths": ["sample.py"],
                    },
                },
                {
                    "type": "agent_call",
                    "agent": "coder",
                    "action": "infer",
                    "prompt": "Set value in sample.py to 42 and create test_sample.py regression. Use the planned scope.",
                },
                {
                    "type": "agent_call",
                    "agent": "verifier",
                    "action": "tool_call",
                    "tool": "run_check",
                    "args": {"check_id": "python-tests", "phase": "verify"},
                },
                {"type": "final", "content": "Applied and verified the value fix."},
            ],
        )
        acquire = model.call

        def observe(history):
            if len(prepared) == 0 and '"executed": false' in json.dumps(history.to_list()).replace(
                '\\"', '"'
            ):
                prepared.append(sample.read_text())
            return acquire(history)

        model.call = observe
        with patch("protoagent_core.agents.architect.create_selected_llm", return_value=model):
            agent = create_architect_agent(
                workspace=str(self.root),
                provider="mock",
                model="offline",
                transport=None,
                record=record,
                subagents=[coder, self.verifier],
                tester_enabled=False,
            )
        self.assertEqual(
            agent.tools["worker_packet"].input_schema["properties"]["role"]["enum"],
            ["explorer", "coder", "tester"],
        )
        configure_agent_context(
            agent, record, compact_protocol=True, cards=[coder.card, self.verifier.card]
        )
        configure_agent_context(coder, record, compact_protocol=True)
        task = Task.create_infer("Fix sample.py so value is 42, with a regression")
        self.context.attach_to_task(task)
        handle = RunHandle.start(agent, task)
        self.handles.append(handle)
        for expected in ("execute_command", "replace_file", "create_file", "execute_command"):
            (approval,) = await self.pending()
            self.assertEqual(approval.request.action.name, expected)
            self.broker.resolve(
                ApprovalDecision(True, approval.request.request_id),
                scope=self.authorization.scope,
                fingerprint=approval.fingerprint,
            )
        result = await handle.result()
        self.assertEqual(result.status, "completed", result.error)
        self.assertEqual(prepared, ["value = 0\n"], "Preparing context must not apply edits")
        accepted = await validate_completion(
            infer_run_contract("Fix sample.py"),
            result.task,
            result.report,
            self.attempt,
            self.broker,
        )
        self.assertTrue(accepted.completion["applied"], accepted.completion)
        self.assertTrue(accepted.completion["verified"], accepted.completion)
        self.assertIn("Ran 1 test", accepted.verification["latest"][0]["stderr"])

    async def test_question_then_check_routing_uses_the_advertised_worker_protocol(self):
        (self.root / "test_sample.py").write_text(
            "import unittest\nclass Regression(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(42, 42)\n"
        )
        record = self.attempt.record
        record.checks = discover_checks(str(self.root))
        record.selected = tuple(record.checks)
        seen = []
        model = create_llm(
            "mock",
            sequential_responses=[
                {
                    "type": "tool_call",
                    "tool": "ask_user",
                    "args": {"question": "Change server.py instead of client.py?"},
                },
                {
                    "type": "tool_call",
                    "tool": "verifier_run_check",
                    "args": {"check_id": "python-tests", "phase": "baseline"},
                },
                {
                    "type": "agent_call",
                    "agent": "verifier",
                    "action": "tool_call",
                    "tool": "run_check",
                    "args": {"check_id": "python-tests", "phase": "baseline"},
                },
                {"type": "final", "content": "Baseline ran one test."},
            ],
        )
        acquire = model.call

        def observe(history):
            seen.append(history.to_list())
            return acquire(history)

        model.call = observe

        async def answer(request):
            return "Change server.py from Starlette to FastAPI"

        with patch("protoagent_core.agents.architect.create_selected_llm", return_value=model):
            agent = create_architect_agent(
                workspace=str(self.root),
                provider="mock",
                model="offline",
                transport=None,
                record=record,
                subagents=[self.verifier],
                tester_enabled=False,
                user_input_handler=answer,
            )
        configure_agent_context(
            agent, record, fallback_window=8192, compact_protocol=True, cards=[self.verifier.card]
        )
        task = Task.create_infer("Change client.py from Starlette to FastAPI")
        self.context.attach_to_task(task)
        handle = RunHandle.start(agent, task)
        self.handles.append(handle)
        (approval,) = await self.pending()
        self.broker.resolve(
            ApprovalDecision(True, approval.request.request_id),
            scope=self.authorization.scope,
            fingerprint=approval.fingerprint,
        )
        result = await handle.result()
        self.assertEqual(result.status, "completed", result.error)
        self.assertIn(
            '"agent":"verifier","action":"tool_call","tool":"run_check"', seen[0][0]["content"]
        )
        self.assertIn("Change server.py from Starlette to FastAPI", json.dumps(seen[2]))
        self.assertEqual(len(self.broker.records(self.authorization.scope)), 1)
        self.assertEqual(
            sum(
                event.payload.get("llm_event_type") == "tool_rejected"
                for event in result.report.events
            ),
            1,
        )

    def planner(self, responses, policy=None, **settings):
        record = self.attempt.record
        record.checks = discover_checks(str(self.root))
        record.selected = tuple(record.checks)
        model = create_llm("mock", sequential_responses=responses, **settings)
        agent = Agent(
            name="architect",
            llm=model,
            policy=policy or CapabilityPolicy({"task.manage": "allow"}),
            verbosity=0,
        )
        add_task_tools(agent, record, "architect")
        configure_agent_context(agent, record, fallback_window=8192, compact_protocol=True)
        return agent

    async def test_successful_actions_do_not_replenish_correction_allowance(self):
        (self.root / "sample.py").write_text("value = 0\n")
        agent = self.planner(
            [
                {
                    "type": "tool_call",
                    "tool": "plan_task",
                    "args": {"paths": [], "criteria": ["Fix value"], "check_ids": ["invented"]},
                },
                {"type": "tool_call", "tool": "task_status", "args": {}},
                {
                    "type": "tool_call",
                    "tool": "plan_task",
                    "args": {"paths": [], "criteria": [], "check_ids": []},
                },
            ],
            max_tool_corrections=1,
        )
        original = self.attempt.record.snapshot()
        result = await RunHandle.start(agent, Task.create_infer("Plan a fix")).result()
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.task.metadata["blockers"][0]["code"], "tool_correction_limit")
        self.assertEqual(result.task.metadata["blockers"][0]["attempts"], 2)
        self.assertEqual(original, self.attempt.record.snapshot())
        self.assertEqual(sum(item.kind == "action_rejection" for item in result.task.artifacts), 2)
        self.assertTrue(
            any(event.type == "tool.correction.exhausted" for event in result.report.events)
        )

    async def test_ordinary_exception_and_denial_do_not_enter_correction(self):
        for denied in (False, True):
            calls = []
            agent = self.planner(
                [{"type": "tool_call", "tool": "explode", "args": {}}],
                policy=CapabilityPolicy({"task.manage": "deny" if denied else "allow"}),
            )

            @agent.tool(capabilities=["task.manage"])
            def explode() -> str:
                calls.append(1)
                raise ValueError("ordinary execution failure")

            result = await RunHandle.start(agent, Task.create_infer("Attempt action")).result()
            self.assertEqual(result.status, "failed")
            self.assertEqual(calls, [] if denied else [1])
            self.assertFalse(any(event.type == "action.rejected" for event in result.report.events))

    async def test_capability_feedback_preserves_completed_read_without_replaying_it(self):
        (self.root / "README.md").write_text("A small task management service.\n")
        explorer = create_explorer_agent(
            provider="mock",
            workspace=str(self.root),
            transport=None,
            record=self.attempt.record,
            prompt_profile="small",
        )
        seen = []
        read = {
            "type": "agent_call",
            "agent": "explorer",
            "action": "tool_call",
            "tool": "read_file",
            "args": {"path": "README.md"},
        }
        model = create_llm(
            "mock",
            sequential_responses=[
                read,
                {
                    "type": "agent_call",
                    "agent": "verifier",
                    "action": "infer",
                    "prompt": "Run tests",
                },
                {
                    "type": "agent_call",
                    "agent": "explorer",
                    "action": "tool_call",
                    "tool": "invented_reader",
                    "args": {},
                },
                read,
                {"type": "final", "content": "A small task management service."},
            ],
        )
        acquire = model.call

        def observe(history):
            seen.append(history.to_list())
            return acquire(history)

        model.call = observe
        agent = Agent(
            name="architect",
            llm=model,
            subagents=[explorer, self.verifier],
            subagent_limits=SubagentLimits(max_concurrency=1, max_depth=1),
            policy=CapabilityPolicy({"agent.delegate": "allow", "workspace.read": "allow"}),
            verbosity=0,
        )
        configure_agent_context(
            agent,
            fallback_window=8192,
            compact_protocol=True,
            cards=[explorer.card, self.verifier.card],
        )
        result = await RunHandle.start(agent, Task.create_infer("Explain the project")).result()
        self.assertEqual(result.status, "completed", result.error)
        self.assertFalse(self.verifier.card.capabilities.supports_inference)
        self.assertTrue(explorer.card.capabilities.supports_inference)
        self.assertIn('"supports_inference":false', seen[0][0]["content"])
        self.assertIn("A small task management service", json.dumps(seen[-1]))
        rejections = [
            event
            for event in result.report.events
            if event.payload.get("llm_event_type") == "agent_call_rejected"
        ]
        self.assertEqual(len(rejections), 2)
        self.assertEqual(
            {event.payload["metadata"]["rejection"]["code"] for event in rejections},
            {"unsupported_inference", "unknown_delegated_tool"},
        )
        reads = [
            event
            for event in result.report.events
            if event.type == "action.completed"
            and isinstance(event.payload.get("action"), dict)
            and event.payload["action"].get("name") == "read_file"
        ]
        self.assertEqual(len(reads), 1)
        self.assertEqual(self.broker.records(self.authorization.scope), ())

    async def test_bad_delegated_check_is_corrected_before_one_approved_execution(self):
        (self.root / "test_sample.py").write_text(
            "import unittest\nclass Regression(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(42, 42)\n"
        )
        record = self.attempt.record
        record.checks = discover_checks(str(self.root))
        record.selected = tuple(record.checks)
        # The application worker uses the newly discovered IDs without reconstructing tools.
        model = create_llm(
            "mock",
            sequential_responses=[
                {
                    "type": "agent_call",
                    "agent": "verifier",
                    "action": "tool_call",
                    "tool": "run_check",
                    "args": {"check_id": "python-tests", "phase": "invented"},
                },
                {
                    "type": "agent_call",
                    "agent": "verifier",
                    "action": "tool_call",
                    "tool": "run_check",
                    "args": {"check_id": "python-tests", "phase": "baseline"},
                },
                {"type": "final", "content": "Baseline ran one test."},
            ],
        )
        agent = Agent(
            name="architect",
            llm=model,
            subagents=[self.verifier],
            subagent_limits=SubagentLimits(max_concurrency=1, max_depth=1),
            policy=CapabilityPolicy({"agent.delegate": "allow", "process.execute": "allow"}),
            verbosity=0,
        )
        configure_agent_context(
            agent, record, fallback_window=8192, compact_protocol=True, cards=[self.verifier.card]
        )
        task = Task.create_infer("Measure the baseline")
        self.context.attach_to_task(task)
        handle = RunHandle.start(agent, task)
        self.handles.append(handle)
        (approval,) = await self.pending()
        self.assertEqual(approval.request.action.metadata["check_id"], "python-tests")
        self.broker.resolve(
            ApprovalDecision(True, approval.request.request_id),
            scope=self.authorization.scope,
            fingerprint=approval.fingerprint,
        )
        result = await handle.result()
        self.assertEqual(result.status, "completed", result.error)
        self.assertEqual(len(self.broker.records(self.authorization.scope)), 1)
        self.assertTrue(any(event.type == "action.rejected" for event in result.report.events))
        self.assertFalse(record.frozen, "A rejected check request cannot freeze the plan")

    async def test_edit_preparation_rejections_do_not_mutate_or_request_approval(self):
        target = self.root / "sample.py"
        source = "value = 0\nvalue = 0\n"
        target.write_text(source)
        revision = hashlib.sha256(source.encode()).hexdigest()
        for expected, old, code in (
            ("stale", source, "stale_source_revision"),
            (revision, "value = 0", "ambiguous_source_match"),
        ):
            result = await self.start_tool(
                self.coder,
                "edit_file",
                {
                    "path": str(target),
                    "old": old,
                    "new": "value = 42",
                    "expected_revision": expected,
                },
            ).result()
            self.assertEqual(result.status, "failed")
            rendered = json.dumps(result.task.to_dict())
            self.assertIn(code, rendered)
            self.assertIn('"changes_made": false', rendered)
            self.assertEqual(target.read_text(), source)
            self.assertEqual(self.broker.records(self.authorization.scope), ())
            self.assertFalse(
                any(event.type == "action.completed" for event in result.report.events)
            )
