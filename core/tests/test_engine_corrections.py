"""Native correction preserves observations, authority and acceptance boundaries."""

import hashlib
import json

from protolink import Agent, ApprovalDecision, CapabilityPolicy, RunHandle, SubagentLimits, Task
from protolink.llms.factory import create_llm
from runtime_support import NativeRuntimeCase

from protoagent_core.agents.explorer import create_explorer_agent
from protoagent_core.request_budget import configure_agent_context
from protoagent_core.task_record import add_task_tools, discover_checks


class EngineCorrectionTests(NativeRuntimeCase):
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
                    "args": {"paths": [], "criteria": ["Fix value"], "check_ids": []},
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
