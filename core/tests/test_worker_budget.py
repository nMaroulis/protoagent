"""Real native worker loops reserve a final turn without inventing completion."""

import asyncio
import hashlib
import json
from unittest.mock import patch

import httpx
from protolink import Agent, ApprovalDecision, RunBudget, RunHandle, Task
from protolink.llms.base import MAX_INFER_STEPS
from protolink.llms.factory import create_llm
from protolink.llms.server.ollama_client import OllamaLLM
from runtime_support import NativeRuntimeCase

from protoagent_core.agents.coder import create_coder_agent
from protoagent_core.request_budget import configure_agent_context
from protoagent_core.run_contracts import infer_run_contract
from protoagent_core.runtime import _run_agent_deck
from protoagent_core.verification import validate_completion


class WorkerBudgetTests(NativeRuntimeCase):
    def setUp(self):
        super().setUp()
        # These exercise the live fallback used by non-checkpointed transports.
        mode = patch.dict("os.environ", {"PROTOAGENT_CHECKPOINTED_WORKERS": "0"})
        mode.start()
        self.addCleanup(mode.stop)

    async def test_tool_calling_model_is_handed_off_without_another_model_reply(self):
        sample = self.root / "sample.py"
        original = "value = 0\nremaining = 0\n" + "# source\n" * MAX_INFER_STEPS
        first = original.replace("value = 0", "value = 42")
        second = first.replace("remaining = 0", "remaining = 99")
        sample.write_text(original)
        read = {"type": "tool_call", "tool": "read_file", "args": {"path": "sample.py"}}

        def edit(old, new, source):
            return {
                "type": "tool_call",
                "tool": "edit_file",
                "args": {
                    "path": "sample.py",
                    "old": old,
                    "new": new,
                    "expected_revision": hashlib.sha256(source.encode()).hexdigest(),
                },
            }

        coder_model = create_llm(
            "mock",
            sequential_responses=[
                read,
                edit("value = 0", "value = 42", original),
                read,
                read,
                read,
                {
                    "type": "tool_call",
                    "tool": "read_file",
                    "args": {"path": "sample.py", "start_line": 2, "end_line": 2},
                },
                edit("remaining = 0", "remaining = 99", first),
                {
                    "type": "tool_call",
                    "tool": "read_file",
                    "args": {"path": "sample.py", "start_line": 1, "end_line": 3},
                },
                edit("remaining = 99", "remaining = 99", second),
                edit("remaining = 99", "remaining = 100", second),
            ],
        )
        architect_model = create_llm(
            "mock",
            sequential_responses=[
                {
                    "type": "tool_call",
                    "tool": "coder",
                    "args": {"task": "Change sample.py value to 42."},
                },
                {
                    "type": "final",
                    "content": "The edit is partial; verification remains outstanding.",
                },
            ],
        )
        with (
            patch("protoagent_core.agents.coder.create_selected_llm", return_value=coder_model),
            patch(
                "protoagent_core.agents.architect.create_selected_llm", return_value=architect_model
            ),
            patch("protoagent_core.agents.explorer.create_selected_llm", return_value=None),
            patch.dict(
                "os.environ",
                {"PROTOAGENT_AGENT_TRANSPORT": "local", "PROTOAGENT_CODER_MAX_WINDOWS": "1"},
            ),
        ):
            running = asyncio.create_task(
                _run_agent_deck(
                    "Change sample.py value to 42",
                    "mock",
                    "offline",
                    str(self.root),
                    None,
                    self.bridge,
                    {"resolved": "small"},
                    tester_enabled=False,
                )
            )
            try:
                approved = set()
                async with asyncio.timeout(3):
                    while len(approved) < 2:
                        if running.done():
                            self.fail(str(await running))
                        if self.bridge.request_path.exists():
                            approval = json.loads(self.bridge.request_path.read_text())
                            if approval["request_id"] not in approved:
                                self.bridge.decision_path.write_text(
                                    json.dumps(
                                        {
                                            "request_id": approval["request_id"],
                                            "fingerprint": approval["fingerprint"],
                                            "approved": True,
                                        }
                                    )
                                )
                                approved.add(approval["request_id"])
                        await asyncio.sleep(0.005)
                result = await asyncio.wait_for(running, 3)
            finally:
                if not running.done():
                    running.cancel()
        self.assertEqual(result["status"], "incomplete", result["answer"])
        self.assertEqual(coder_model._current_seq_idx, MAX_INFER_STEPS - 1)
        self.assertIn("Changes already applied: sample.py", result["answer"])
        self.assertIn("edits may be partial", result["answer"])
        self.assertNotIn("{'code':", result["answer"])
        self.assertTrue(result["completion_validation"]["applied"])
        self.assertFalse(result["completion_validation"]["satisfied"])
        self.assertEqual(
            result["task_record"]["worker_reports"]["coder"]["reason"], "worker_turn_limit"
        )
        self.assertTrue(any(event["type"] == "worker.handoff" for event in result["run_events"]))
        self.assertFalse(any(event["type"] == "action.failed" for event in result["run_events"]))
        self.assertEqual(len(result["approval_requests"]), 2)
        self.assertEqual(result["workflow_continuations"], 2)
        self.assertIn("unchanged_source", json.dumps(result["run_report"]))
        self.assertFalse(any(event["type"] == "process.started" for event in result["run_events"]))
        self.assertEqual(sample.read_text(), second)

    async def test_workflow_continues_premature_final_and_finishes_without_replaying_edits(self):
        original = "value = 0\nremaining = 0\n" + "# source\n" * MAX_INFER_STEPS
        first = original.replace("value = 0", "value = 42")
        (self.root / "sample.py").write_text(original)
        (self.root / "test_sample.py").write_text(
            "import unittest\nfrom sample import value, remaining\n"
            "class Regression(unittest.TestCase):\n"
            "    def test_values(self):\n        self.assertEqual((value, remaining), (42, 99))\n"
        )
        coder_model = create_llm(
            "mock",
            sequential_responses=[
                {
                    "type": "tool_call",
                    "tool": "replace_file",
                    "args": {"path": "sample.py", "content": first},
                },
                *[
                    {
                        "type": "tool_call",
                        "tool": "read_file",
                        "args": {"path": "sample.py", "start_line": line, "end_line": line},
                    }
                    for line in range(2, MAX_INFER_STEPS)
                ],
                {"type": "tool_call", "tool": "read_file", "args": {"path": "sample.py"}},
                {
                    "type": "tool_call",
                    "tool": "edit_file",
                    "args": {
                        "path": "sample.py",
                        "old": "remaining = 0",
                        "new": "remaining = 99",
                        "expected_revision": hashlib.sha256(first.encode()).hexdigest(),
                    },
                },
                {"type": "final", "content": "Finished the remaining edit."},
            ],
        )
        seen = []
        replies = iter(
            [
                {
                    "type": "tool_call",
                    "tool": "coder",
                    "args": {"task": "Update both values in sample.py."},
                },
                {
                    "type": "final",
                    "content": 'I will read next. <channel|>{"type":"tool_call","tool":"read_file","args":{"path":"never-read.txt"}}',
                },
                {
                    "type": "tool_call",
                    "tool": "coder",
                    "args": {"task": "Value is already 42. Finish only remaining=99 in sample.py."},
                },
                {
                    "type": "tool_call",
                    "tool": "run_check",
                    "args": {"check_id": "python-tests", "phase": "verify"},
                },
                {"type": "final", "content": "Both values applied and checked."},
            ]
        )

        def reply(history, prompt):
            seen.append(str(history.to_list()))
            return next(replies)

        with (
            patch("protoagent_core.agents.coder.create_selected_llm", return_value=coder_model),
            patch(
                "protoagent_core.agents.architect.create_selected_llm",
                return_value=create_llm("mock", response_callback=reply),
            ),
            patch("protoagent_core.agents.explorer.create_selected_llm", return_value=None),
            patch.dict(
                "os.environ",
                {"PROTOAGENT_AGENT_TRANSPORT": "local", "PROTOAGENT_CODER_MAX_WINDOWS": "1"},
            ),
        ):
            running = asyncio.create_task(
                _run_agent_deck(
                    "Fix both values in sample.py",
                    "mock",
                    "offline",
                    str(self.root),
                    None,
                    self.bridge,
                    {"resolved": "small"},
                    tester_enabled=False,
                )
            )
            approved = set()
            try:
                async with asyncio.timeout(5):
                    while not running.done():
                        if self.bridge.request_path.exists():
                            request = json.loads(self.bridge.request_path.read_text())
                            if request["request_id"] not in approved:
                                self.bridge.decision_path.write_text(
                                    json.dumps(
                                        {
                                            "request_id": request["request_id"],
                                            "fingerprint": request["fingerprint"],
                                            "approved": True,
                                        }
                                    )
                                )
                                approved.add(request["request_id"])
                        await asyncio.sleep(0.005)
                    result = await running
            finally:
                if not running.done():
                    running.cancel()
                    await asyncio.gather(running, return_exceptions=True)
        self.assertEqual(result["status"], "completed", result["answer"])
        self.assertEqual(result["workflow_continuations"], 2)
        progress = [json.loads(line) for line in self.bridge.progress_path.read_text().splitlines()]
        event_ids = [item["run_event"]["event_id"] for item in progress if "run_event" in item]
        self.assertEqual(
            len(event_ids), len(set(event_ids)), "Continuations must not repeat progress"
        )
        self.assertEqual(len(result["approval_requests"]), 3)
        self.assertEqual(
            result["verification"]["attempts"], 1, "Continuation must not open a repair phase"
        )
        self.assertTrue(result["completion_validation"]["satisfied"])
        handoff_time = next(
            event["timestamp"]
            for event in result["run_events"]
            if event["type"] == "worker.handoff"
        )
        continuation_time = next(
            event["timestamp"]
            for event in result["run_events"]
            if event["type"] == "workflow.continuing"
        )
        self.assertFalse(
            any(
                event["type"] == "llm.call.started"
                and event["agent_name"] == "architect"
                and handoff_time < event["timestamp"] < continuation_time
                for event in result["run_events"]
            ),
            "Architect must yield before proposing a routine continuation question",
        )
        self.assertIn("Continue the current edit phase", seen[2])
        self.assertFalse(
            any(
                event["type"] == "action.requested" and "never-read.txt" in str(event["payload"])
                for event in result["run_events"]
            ),
            "Embedded tool JSON is data, never an executed continuation",
        )
        self.assertEqual(
            (self.root / "sample.py").read_text(), first.replace("remaining = 0", "remaining = 99")
        )

    def make_coder(self, model):
        with patch("protoagent_core.agents.coder.create_selected_llm", return_value=model):
            agent = create_coder_agent(
                workspace=str(self.root),
                transport=None,
                prompt_profile="small",
                approval_handler=self.broker,
                authorization=self.authorization,
                attempt=self.attempt,
                checkpoints=self.checkpoints,
            )
        configure_agent_context(agent, self.attempt.record, compact_protocol=True)
        return agent

    def start_coder(self, agent, *, budget=None):
        task = Task.create_infer("Change sample.py")
        context = self.context.child(agent_name="coder")
        if budget:
            context.budget = budget
        context.attach_to_task(task)
        handle = RunHandle.start(agent, task)
        self.handles.append(handle)
        return handle

    async def finish_coder(self, handle, *, deny_after=None):
        running = asyncio.create_task(handle.result())
        count = 0
        async with asyncio.timeout(4):
            while not running.done():
                for approval in self.broker.pending(self.authorization.scope):
                    count += 1
                    self.broker.resolve(
                        ApprovalDecision(
                            deny_after is None or count <= deny_after, approval.request.request_id
                        ),
                        scope=self.authorization.scope,
                        fingerprint=approval.fingerprint,
                    )
                await asyncio.sleep(0.005)
            return await running

    @staticmethod
    def read_window():
        return [
            {"type": "tool_call", "tool": "read_file", "args": {"path": "sample.py"}}
            for _ in range(MAX_INFER_STEPS - 2)
        ]

    @staticmethod
    def write_value(value):
        return {
            "type": "tool_call",
            "tool": "replace_file",
            "args": {"path": "sample.py", "content": f"value = {value}\n"},
        }

    async def test_fresh_writes_continue_in_same_assignment_without_replaying_approvals(self):
        sample = self.root / "sample.py"
        sample.write_text("value = 0\n")
        model = create_llm(
            "mock",
            sequential_responses=[
                self.write_value(1),
                *self.read_window(),
                self.write_value(42),
                {"type": "final", "content": "Applied the remaining change."},
            ],
        )
        result = await self.finish_coder(self.start_coder(self.make_coder(model)))
        self.assertEqual(result.status, "completed", result.error)
        self.assertEqual(sample.read_text(), "value = 42\n")
        self.assertEqual(model._current_seq_idx, MAX_INFER_STEPS + 1)
        self.assertEqual(len(self.broker.records(self.authorization.scope)), 2)
        self.assertEqual(
            sum(event.type == "worker.continuing" for event in result.report.events), 1
        )
        self.assertFalse(any(event.type == "worker.handoff" for event in result.report.events))

    async def test_read_only_second_window_cannot_reuse_previous_progress(self):
        (self.root / "sample.py").write_text("value = 0\n")
        read = {"type": "tool_call", "tool": "read_file", "args": {"path": "sample.py"}}
        model = create_llm(
            "mock",
            sequential_responses=[
                self.write_value(1),
                *self.read_window(),
                *([read] * MAX_INFER_STEPS),
            ],
        )
        result = await self.finish_coder(self.start_coder(self.make_coder(model)))
        self.assertEqual(result.status, "completed", result.error)
        self.assertEqual(model._current_seq_idx, 2 * (MAX_INFER_STEPS - 1))
        report = self.attempt.record.reports["coder"]
        self.assertEqual(report["reason"], "worker_no_progress")
        self.assertEqual(report["work_windows"], 2)
        self.assertEqual(report["applied_paths"], [str(self.root / "sample.py")])
        self.assertEqual(len(self.broker.records(self.authorization.scope)), 1)
        self.assertEqual(
            sum(event.type == "worker.continuing" for event in result.report.events), 1
        )

    async def test_three_progressing_windows_are_bounded_by_the_application(self):
        (self.root / "sample.py").write_text("value = 0\n")
        responses = [
            action
            for value in (1, 2, 3)
            for action in [self.write_value(value), *self.read_window()]
        ]
        model = create_llm("mock", sequential_responses=responses)
        result = await self.finish_coder(self.start_coder(self.make_coder(model)))
        self.assertEqual(result.status, "completed", result.error)
        self.assertEqual(model._current_seq_idx, 3 * (MAX_INFER_STEPS - 1))
        report = self.attempt.record.reports["coder"]
        self.assertEqual(report["reason"], "worker_turn_limit")
        self.assertEqual(report["work_windows"], 3)
        self.assertEqual(report["model_turns"], 27)
        self.assertEqual(len(self.broker.records(self.authorization.scope)), 3)

    async def test_denial_in_second_window_is_terminal_and_keeps_first_write(self):
        sample = self.root / "sample.py"
        sample.write_text("value = 0\n")
        model = create_llm(
            "mock",
            sequential_responses=[self.write_value(1), *self.read_window(), self.write_value(42)],
        )
        result = await self.finish_coder(self.start_coder(self.make_coder(model)), deny_after=1)
        self.assertEqual(result.status, "failed")
        self.assertEqual(sample.read_text(), "value = 1\n")
        self.assertEqual(model._current_seq_idx, MAX_INFER_STEPS)
        self.assertEqual(len(self.broker.records(self.authorization.scope)), 2)
        self.assertFalse(any(event.type == "worker.handoff" for event in result.report.events))

    async def test_explicit_missing_context_is_retained_and_prevents_another_window(self):
        (self.root / "sample.py").write_text("value = 0\n")
        model = create_llm(
            "mock",
            sequential_responses=[
                self.write_value(1),
                {
                    "type": "tool_call",
                    "tool": "report_task",
                    "args": {
                        "status": "needs_context",
                        "summary": "Need the response contract",
                        "missing_paths": ["api_contract.md"],
                    },
                },
                *self.read_window(),
                self.write_value(42),
            ],
        )
        result = await self.finish_coder(self.start_coder(self.make_coder(model)))
        self.assertEqual(result.status, "completed", result.error)
        report = self.attempt.record.reports["coder"]
        self.assertEqual(report["status"], "needs_context")
        self.assertEqual(report["summary"], "Need the response contract")
        self.assertEqual(report["missing_paths"], [str(self.root / "api_contract.md")])
        self.assertEqual(report["reason"], "worker_report")
        self.assertEqual(model._current_seq_idx, MAX_INFER_STEPS - 1)
        self.assertFalse(any(event.type == "worker.continuing" for event in result.report.events))

    async def test_external_edit_blocks_window_continuation(self):
        sample = self.root / "sample.py"
        sample.write_text("value = 0\n")
        replies = iter([self.write_value(1), *self.read_window(), self.write_value(42)])
        calls = []

        def reply(history, prompt):
            calls.append(prompt)
            if len(calls) == MAX_INFER_STEPS - 1:
                sample.write_text("value = 'external edit'\n")
            return next(replies)

        model = create_llm("mock", response_callback=reply)
        result = await self.finish_coder(self.start_coder(self.make_coder(model)))
        self.assertEqual(result.status, "completed", result.error)
        self.assertEqual(sample.read_text(), "value = 'external edit'\n")
        self.assertEqual(len(calls), MAX_INFER_STEPS - 1)
        self.assertEqual(len(self.broker.records(self.authorization.scope)), 1)
        self.assertFalse(any(event.type == "worker.continuing" for event in result.report.events))

    async def test_denied_write_remains_a_native_failure_without_handoff_or_replay(self):
        sample = self.root / "sample.py"
        sample.write_text("value = 0\n")
        model = create_llm(
            "mock",
            sequential_responses=[
                {
                    "type": "tool_call",
                    "tool": "replace_file",
                    "args": {"path": "sample.py", "content": "value = 42\n"},
                },
                {"type": "final", "content": "Must never be requested"},
            ],
        )
        handle = self.start_coder(self.make_coder(model))
        (approval,) = await self.pending()
        self.broker.resolve(
            ApprovalDecision(False, approval.request.request_id),
            scope=self.authorization.scope,
            fingerprint=approval.fingerprint,
        )
        result = await handle.result()
        self.assertEqual(result.status, "failed")
        self.assertEqual(model._current_seq_idx, 1)
        self.assertEqual(sample.read_text(), "value = 0\n")
        self.assertTrue(any(event.type == "action.denied" for event in result.report.events))
        self.assertFalse(any(event.type == "worker.handoff" for event in result.report.events))

    async def test_provider_repeat_abort_discards_pending_native_edit_and_retains_prior_write(self):
        sample = self.root / "sample.py"
        sample.write_text("value = 0\n")
        requests = []

        def respond(request):
            requests.append(json.loads(request.content))
            first = len(requests) == 1
            call = {
                "name": "replace_file" if first else "edit_file",
                "arguments": {"path": "sample.py", "content": "value = 42\n"}
                if first
                else {
                    "path": "sample.py",
                    "old": "value = 42",
                    "new": "value = 43",
                    "expected_revision": hashlib.sha256(b"value = 42\n").hexdigest(),
                },
            }
            chunk = {"message": {"tool_calls": [{"function": call}]}, "done": first}
            stream = json.dumps(chunk) + "\n"
            if not first:
                stream += (
                    json.dumps({"error": "prediction aborted, token repeat limit reached"}) + "\n"
                )
            return httpx.Response(200, content=stream)

        client = httpx.AsyncClient
        with (
            patch.object(OllamaLLM, "validate_connection", return_value=True),
            patch(
                "httpx.AsyncClient",
                side_effect=lambda **kw: client(transport=httpx.MockTransport(respond), **kw),
            ),
        ):
            model = OllamaLLM(
                base_url="http://provider.invalid", model="small", supports_tool_calling=True
            )
            handle = self.start_coder(self.make_coder(model))
            (approval,) = await self.pending()
            self.broker.resolve(
                ApprovalDecision(True, approval.request.request_id),
                scope=self.authorization.scope,
                fingerprint=approval.fingerprint,
            )
            result = await handle.result()
        self.assertEqual(result.status, "failed")
        self.assertIn("token repeat limit", str(result.error))
        self.assertEqual(len(requests), 2, "A generation abort must not replay committed edits")
        self.assertEqual(
            sample.read_text(), "value = 42\n", "The interrupted second proposal must never execute"
        )
        self.assertEqual(len(self.broker.records(self.authorization.scope)), 1)
        self.assertFalse(any(event.type == "worker.handoff" for event in result.report.events))

    async def test_shared_budget_exhaustion_is_not_converted_into_a_worker_handoff(self):
        (self.root / "sample.py").write_text("value = 0\n# source\n")
        model = create_llm(
            "mock",
            sequential_responses=[
                {
                    "type": "tool_call",
                    "tool": "read_file",
                    "args": {"path": "sample.py", "start_line": line, "end_line": line},
                }
                for line in (1, 2)
            ],
        )
        result = await self.start_coder(
            self.make_coder(model),
            budget=RunBudget(max_tool_calls=1),
        ).result()
        self.assertEqual(result.status, "failed")
        self.assertTrue(any(event.type == "budget.exceeded" for event in result.report.events))
        self.assertFalse(any(event.type == "worker.handoff" for event in result.report.events))

    async def test_normal_agent_call_route_receives_the_same_native_handoff(self):
        (self.root / "sample.py").write_text("# source\n" * MAX_INFER_STEPS)
        model = create_llm(
            "mock",
            sequential_responses=[
                {
                    "type": "tool_call",
                    "tool": "read_file",
                    "args": {"path": "sample.py", "start_line": line, "end_line": line},
                }
                for line in range(1, MAX_INFER_STEPS + 1)
            ],
        )
        child = self.make_coder(model)
        parent = Agent(
            name="architect",
            verbosity=0,
            subagents=[child],
            llm=create_llm(
                "mock",
                sequential_responses=[
                    {
                        "type": "agent_call",
                        "agent": "coder",
                        "action": "infer",
                        "prompt": "Change sample.py",
                    },
                    {"type": "final", "content": "Coder needs a more focused assignment."},
                ],
            ),
        )
        task = Task.create_infer("Change sample.py")
        self.context.attach_to_task(task)
        handle = RunHandle.start(parent, task)
        self.handles.append(handle)
        result = await handle.result()
        self.assertEqual(result.status, "completed", result.error)
        self.assertEqual(model._current_seq_idx, MAX_INFER_STEPS - 1)
        self.assertEqual(self.attempt.record.reports["coder"]["reason"], "worker_no_progress")
        self.assertTrue(
            any(
                event.type == "worker.handoff" and event.agent_name == "coder"
                for event in result.report.events
            )
        )
        self.assertEqual(len(result.task.metadata["subagent_runs"]), 1)

    async def test_no_progress_handoff_is_deterministic_in_native_and_json_modes(self):
        (self.root / "sample.py").write_text("# source\n" * MAX_INFER_STEPS)
        for native in (False, True):
            with self.subTest(native=native):
                requests = []

                def respond(request):
                    body = json.loads(request.content)
                    requests.append(body)
                    step = len(requests)
                    self.assertLess(step, MAX_INFER_STEPS, "The last model round must never happen")
                    self.assertTrue(body.get("tools") or not native)
                    action = {
                        "type": "tool_call",
                        "tool": "read_file",
                        "args": {"path": "sample.py", "start_line": step, "end_line": step},
                    }
                    message = {"content": json.dumps(action)}
                    if native:
                        message = (
                            {"content": action["content"]}
                            if action["type"] == "final"
                            else {
                                "tool_calls": [
                                    {
                                        "function": {
                                            "name": action["tool"],
                                            "arguments": action["args"],
                                        }
                                    }
                                ]
                            }
                        )
                    return httpx.Response(
                        200, content=json.dumps({"message": message, "done": True}) + "\n"
                    )

                client = httpx.AsyncClient
                with (
                    patch.object(OllamaLLM, "validate_connection", return_value=True),
                    patch(
                        "httpx.AsyncClient",
                        side_effect=lambda **kw: client(
                            transport=httpx.MockTransport(respond), **kw
                        ),
                    ),
                    patch(
                        "protoagent_core.agents.coder.create_selected_llm",
                        return_value=OllamaLLM(
                            base_url="http://provider.invalid",
                            model="small",
                            supports_tool_calling=native,
                        ),
                    ),
                ):
                    agent = create_coder_agent(
                        workspace=str(self.root),
                        transport=None,
                        prompt_profile="small",
                        approval_handler=self.broker,
                        authorization=self.authorization,
                        attempt=self.attempt,
                        checkpoints=self.checkpoints,
                    )
                    configure_agent_context(agent, self.attempt.record, compact_protocol=True)
                    task = Task.create_infer("Change sample.py")
                    self.context.child(agent_name="coder").attach_to_task(task)
                    handle = RunHandle.start(agent, task)
                    self.handles.append(handle)
                    result = await handle.result()
                self.assertEqual(result.status, "completed", result.error)
                self.assertEqual(len(requests), MAX_INFER_STEPS - 1)
                self.assertEqual(result.output["status"], "blocked")
                self.assertFalse(result.output["verified"])
                self.assertEqual(
                    self.attempt.record.reports["coder"]["reason"], "worker_no_progress"
                )
                self.assertTrue(
                    any(event.type == "worker.handoff" for event in result.report.events)
                )
                accepted = await validate_completion(
                    infer_run_contract("Change sample.py"),
                    result.task,
                    result.report,
                    self.attempt,
                    self.broker,
                )
                self.assertFalse(accepted.completion["satisfied"])
                self.assertIn("unresolved worker: coder", json.dumps(accepted.completion))
                self.assertFalse(self.broker.records(self.authorization.scope))


if __name__ == "__main__":
    import unittest

    unittest.main()
