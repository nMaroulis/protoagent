"""The UI answers native questions without owning execution or permissions."""

import asyncio
import json
from dataclasses import replace
from unittest.mock import patch

from protolink import RunBudget, RunHandle, Task, create_llm
from protolink.tools.builtins import ask_user_tool
from runtime_support import NativeRuntimeCase

from protoagent_core.agents.architect import create_architect_agent
from protoagent_core.agents.deck import create_agent_deck
from protoagent_core.runtime import _run_agent_deck
from protoagent_core.runtime_bridge import RuntimeBridge
from protoagent_core.user_input import MAX_ANSWER_CHARS


class UserInputTests(NativeRuntimeCase):
    def controller(self, responses=None, *, bridge=None, callback=None):
        model = create_llm(
            "mock",
            sequential_responses=responses
            or [
                {
                    "type": "tool_call",
                    "tool": "ask_user",
                    "args": {"question": "Format?", "options": ["JSON", "CSV"]},
                },
                {"type": "final", "content": "continued"},
            ],
            response_callback=callback,
        )
        with patch("protoagent_core.agents.architect.create_selected_llm", return_value=model):
            return create_architect_agent(
                workspace=str(self.root),
                transport=None,
                user_input_handler=(bridge or self.bridge).ask_user,
                record=self.attempt.record,
            )

    async def question(self):
        async with asyncio.timeout(3):
            while not self.bridge.input_request_path.exists():
                await asyncio.sleep(0.005)
        return json.loads(self.bridge.input_request_path.read_text())

    def answer(self, request, answer, **overrides):
        self.bridge.input_response_path.write_text(
            json.dumps(
                {
                    **{
                        key: request[key]
                        for key in ("request_id", "run_id", "task_id", "action_id")
                    },
                    "answer": answer,
                    **overrides,
                }
            )
        )

    def start(self, agent, *, budget=None):
        task = Task.create_infer("Ask me about the output format", budget=budget)
        (replace(self.context, budget=budget) if budget else self.context).attach_to_task(task)
        handle = RunHandle.start(agent, task, redaction_policy=self.redaction)
        self.handles.append(handle)
        return handle

    async def test_native_answer_is_observed_before_continuation_with_same_task(self):
        seen = []

        def script(history, prompt):
            observations = [
                json.loads(message["content"])
                for message in history.to_list()
                if message["content"].startswith('{"type": "tool_result"')
            ]
            if observations:
                seen.extend(observations)
                return {"type": "final", "content": "continued with the answer"}
            return {
                "type": "tool_call",
                "tool": "ask_user",
                "args": {"question": "Format?", "options": ["JSON", "CSV"]},
            }

        agent = self.controller(callback=script)
        handle = self.start(agent)
        request = await self.question()
        self.assertEqual(request["question"], "Format?")
        self.answer(request, "plain text please")
        result = await asyncio.wait_for(handle.result(), 3)
        self.assertEqual(result.status, "completed", result.error)
        self.assertIn("plain text please", json.dumps(seen))
        clarification = {"question": "Format?", "answer": "plain text please"}
        self.assertEqual(
            self.attempt.record.snapshot()["user_clarifications"],
            {request["request_id"]: clarification},
        )
        packet = self.attempt.record.packet("coder", "Use the chosen format", [])
        self.assertEqual(packet["user_clarifications"], {request["request_id"]: clarification})
        self.assertFalse(self.attempt.record.frozen)
        events = result.report.events
        self.assertEqual(sum(event.type == "user_input.requested" for event in events), 1)
        self.assertEqual(sum(event.type == "user_input.answered" for event in events), 1)
        self.assertEqual(
            {event.task_id for event in events if event.type.startswith("user_input.")},
            {handle.task.id},
        )
        self.assertFalse(self.bridge.input_request_path.exists())
        self.assertFalse(self.bridge.input_response_path.exists())

    async def test_stale_malformed_blank_and_oversized_answers_do_not_continue(self):
        handle = self.start(self.controller())
        request = await self.question()
        invalid = [
            {"answer": "text", "run_id": "foreign"},
            {"answer": "text", "request_id": "old-question"},
            {"answer": "text", "task_id": "foreign"},
            {"answer": "text", "action_id": "foreign"},
            {"approved": True},
            {"answer": True},
            {"answer": " "},
            {"answer": "x" * (MAX_ANSWER_CHARS + 1)},
        ]
        for generation, response in enumerate(invalid, start=1):
            base = {key: request[key] for key in ("request_id", "run_id", "task_id", "action_id")}
            self.bridge.input_response_path.write_text(json.dumps({**base, **response}))
            async with asyncio.timeout(3):
                while (
                    json.loads(self.bridge.input_request_path.read_text())["presentation_id"]
                    != generation
                ):
                    await asyncio.sleep(0.005)
            self.assertFalse(
                any(event.type == "user_input.answered" for event in handle.report.events)
            )
        self.answer(request, None)
        result = await asyncio.wait_for(handle.result(), 3)
        self.assertEqual(result.status, "completed", result.error)
        self.assertTrue(any(event.type == "user_input.declined" for event in result.report.events))
        self.assertEqual(self.attempt.record.clarifications, {})

    async def test_native_timeout_closes_ui_and_returns_no_invented_answer(self):
        agent = self.controller()
        agent.add_tool(ask_user_tool(self.bridge.ask_user, timeout_seconds=0.08))
        result_task = asyncio.create_task(self.start(agent).result())
        await self.question()
        result = await asyncio.wait_for(result_task, 3)
        self.assertEqual(result.status, "completed", result.error)
        self.assertTrue(any(event.type == "user_input.timed_out" for event in result.report.events))
        self.assertFalse(self.bridge.input_request_path.exists())

    async def test_cancel_pending_native_question_drains_the_callback(self):
        handle = self.start(self.controller())
        controls = asyncio.create_task(self.bridge.serve(handle))
        try:
            await self.question()
            self.bridge.cancel_path.write_text(json.dumps({"reason": "user canceled the question"}))
            await asyncio.wait_for(controls, 3)
            result = await asyncio.wait_for(handle.result(), 3)
            self.assertEqual(result.status, "canceled", result.error)
            self.assertTrue(
                any(event.type == "user_input.canceled" for event in result.report.events)
            )
            self.assertFalse(self.bridge.input_request_path.exists())
        finally:
            controls.cancel()
            await asyncio.gather(controls, return_exceptions=True)

    async def test_native_runtime_budget_expires_during_question(self):
        handle = self.start(self.controller(), budget=RunBudget(max_runtime_seconds=0.12))
        await self.question()
        result = await asyncio.wait_for(handle.result(), 3)
        self.assertNotEqual(result.status, "completed")
        self.assertRegex(str(result.error), "(?i)(budget|runtime|deadline)")
        self.assertFalse(self.bridge.input_request_path.exists())

    async def test_headless_questions_decline_and_do_not_authorize_later_writes(self):
        self.assertEqual(
            (await self.start(self.controller(bridge=RuntimeBridge(None))).result()).status,
            "completed",
        )
        agent = self.controller(
            [
                {"type": "tool_call", "tool": "ask_user", "args": {"question": "Which content?"}},
                {
                    "type": "agent_call",
                    "agent": "coder",
                    "action": "tool_call",
                    "tool": "create_file",
                    "args": {"path": str(self.root / "choice"), "content": "chosen"},
                },
                {"type": "final", "content": "done"},
            ]
        )
        from protolink import SubagentLimits

        agent.subagents = {"coder": self.coder}
        agent.subagent_limits = SubagentLimits(max_children=1)
        handle = self.start(agent)
        request = await self.question()
        self.answer(request, "yes, chosen")
        (record,) = await self.pending()
        self.assertFalse((self.root / "choice").exists())
        self.assertEqual(record.request.action.name, "create_file")
        await handle.cancel("No execution approval provided")
        await handle.result()

    async def test_embedded_workflow_uses_native_question_then_continues(self):
        model = create_llm(
            "mock",
            sequential_responses=[
                {
                    "type": "tool_call",
                    "tool": "ask_user",
                    "args": {"question": "Detailed or concise?"},
                },
                {"type": "final", "content": "A concise answer."},
            ],
        )
        with (
            patch.dict("os.environ", {"PROTOAGENT_AGENT_TRANSPORT": "local"}),
            patch("protoagent_core.agents.architect.create_selected_llm", return_value=model),
            patch("protoagent_core.agents.coder.create_selected_llm", return_value=None),
            patch("protoagent_core.agents.explorer.create_selected_llm", return_value=None),
            patch("protoagent_core.agents.tester.create_selected_llm", return_value=None),
        ):
            running = asyncio.create_task(
                _run_agent_deck(
                    "Explain this repository",
                    "mock",
                    "offline",
                    str(self.root),
                    "question-test",
                    self.bridge,
                    {"resolved": "small", "label": "Small"},
                )
            )
            try:
                request = await self.question()
                self.answer(request, "concise")
                result = await asyncio.wait_for(running, 3)
            finally:
                if not running.done():
                    running.cancel()
                await asyncio.gather(running, return_exceptions=True)
        self.assertEqual(result["status"], "completed", result.get("answer"))
        self.assertIn("A concise answer.", result["answer"])
        self.assertTrue(
            any(event["type"] == "user_input.answered" for event in result["run_events"])
        )
        self.assertEqual(
            result["task_record"]["user_clarifications"][request["request_id"]],
            {"question": "Detailed or concise?", "answer": "concise"},
        )

    def test_only_controller_exposes_question_tool_and_capability(self):
        with patch("protoagent_core.agents.common.create_llm_from_config", return_value=None):
            deck = create_agent_deck(provider="mock", transport=None, local_children=True)
        for name, agent in deck.items():
            self.assertEqual("ask_user" in agent.tools, name == "architect")
        self.assertEqual(deck["architect"].action_authorizer.policy.rules["user.interact"], "allow")
