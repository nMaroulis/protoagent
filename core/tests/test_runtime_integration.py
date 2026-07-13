from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from protolink import ActionDeniedError, RunAction, RunBudget, RunContext

import protoagent_core.agents.architect as architect_module
import protoagent_core.agents.coder as coder_module
import protoagent_core.agents.explorer as explorer_module
from protoagent_core.agents.common import create_runtime_auth
from protoagent_core.agents.deck import create_agent_deck
from protoagent_core.runtime import (
    _authenticated_client_transport,
    _context_from_delivery,
    _monitor_cancellation,
    _preflight_cancellation_result,
    _run_budget,
    _send_task_streaming,
    _transport_report,
)
from protoagent_core.runtime_bridge import RuntimeBridge


class RuntimeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._create_architect_llm = architect_module.create_selected_llm
        self._create_selected_llm = coder_module.create_selected_llm
        self._create_explorer_llm = explorer_module.create_selected_llm
        architect_module.create_selected_llm = lambda *_args, **_kwargs: None
        coder_module.create_selected_llm = lambda *_args, **_kwargs: None
        explorer_module.create_selected_llm = lambda *_args, **_kwargs: None

    def tearDown(self) -> None:
        architect_module.create_selected_llm = self._create_architect_llm
        coder_module.create_selected_llm = self._create_selected_llm
        explorer_module.create_selected_llm = self._create_explorer_llm

    def test_explorer_uses_inferred_json_schema_defaults(self) -> None:
        agent = explorer_module.create_explorer_agent(workspace=".", transport="http")
        schema = agent.tools["search_regex"].input_schema
        self.assertEqual(schema["required"], ["pattern"])
        self.assertEqual(schema["properties"]["path"]["default"], ".")
        self.assertEqual(schema["properties"]["file_filter"]["default"], ".*")

    async def test_explorer_policy_denies_unmatched_capability_by_default(self) -> None:
        agent = explorer_module.create_explorer_agent(workspace=".", transport="http")
        action = RunAction(
            kind="shell.execute",
            name="run_shell",
            capabilities=frozenset({"shell.execute"}),
        )
        with self.assertRaises(ActionDeniedError):
            await agent.authorize_action(action, RunContext(session_id="session-test"))

    async def test_state_compaction_control_plane_is_agent_authorized(self) -> None:
        agent = architect_module.create_architect_agent(workspace=".", transport="http")
        action = RunAction(
            kind="state.compact",
            name="compact_state",
            capabilities=frozenset({"state.compact", "llm.history.compact"}),
        )
        authorization = await agent.authorize_action(action, RunContext(session_id="session-test"))
        self.assertEqual(authorization.action.name, "compact_state")

    def test_explorer_and_coder_are_stateless_workers(self) -> None:
        explorer = explorer_module.create_explorer_agent(workspace=".", transport="http")
        coder = coder_module.create_coder_agent(workspace=".", transport="http")

        self.assertEqual(explorer.storage.__class__.__name__, "InMemoryStorage")
        self.assertEqual(coder.storage.__class__.__name__, "InMemoryStorage")

    def test_agent_deck_uses_shared_protolink_auth(self) -> None:
        auth = create_runtime_auth()
        deck = create_agent_deck(workspace=".", transport="http", auth=auth)

        for agent in deck.values():
            self.assertIs(agent.authenticator, auth.authenticator)
            self.assertEqual(agent.credentials, auth.credentials)
            self.assertIs(agent.transport.authenticator, auth.authenticator)
            self.assertEqual(agent.transport.credentials, auth.credentials)
            self.assertIn("apiKey", agent.card.security_schemes)
            self.assertTrue(agent.transport.config.collect_metrics)
            self.assertTrue(agent.transport.capabilities.networked)
            self.assertEqual(agent.transport.metrics.requests_started, 0)

        report = _transport_report(
            SimpleNamespace(transport=deck["architect"].transport),
            deck,
            deck["architect"].transport,
        )
        self.assertEqual(report["client"]["transport"], "http")
        self.assertTrue(report["client"]["config"]["collect_metrics"])
        self.assertEqual(report["agents"]["coder"]["metrics"]["requests_started"], 0)

    def test_runtime_client_transport_uses_shared_protolink_auth(self) -> None:
        auth = create_runtime_auth()
        transport = _authenticated_client_transport(
            "sse",
            "http://127.0.0.1:9999",
            auth,
            timeout=1,
        )

        self.assertIs(transport.authenticator, auth.authenticator)
        self.assertEqual(transport.credentials, auth.credentials)
        self.assertIsNone(transport.security_context)
        self.assertTrue(transport.config.collect_metrics)
        self.assertTrue(transport.capabilities.streaming)

    def test_run_budget_uses_protolink_budget_carrier(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "PROTOAGENT_RUN_MAX_STEPS": "12",
                "PROTOAGENT_RUN_MAX_INPUT_TOKENS": "16000",
                "PROTOAGENT_RUN_MAX_OUTPUT_TOKENS": "2048",
                "PROTOAGENT_RUN_MAX_SECONDS": "45",
            },
        ):
            budget = _run_budget("openai", "gpt-test", RunBudget)

        self.assertEqual(budget.max_steps, 12)
        self.assertEqual(budget.max_runtime_seconds, 45.0)
        self.assertEqual(budget.max_input_tokens, 16000)
        self.assertEqual(budget.max_output_tokens, 2048)
        self.assertEqual(budget.metadata["source"], "protoagent-runtime")

    def test_final_canceled_context_is_read_from_delivery(self) -> None:
        context = RunContext(session_id="session-test").cancel("Stopped by test")
        final_context = _context_from_delivery(
            {"task": {"metadata": {"run_context": context.to_dict()}}}
        )
        self.assertTrue(final_context.canceled)
        self.assertEqual(final_context.cancel_reason, "Stopped by test")

    def test_runtime_bridge_preserves_cancel_requested_before_startup(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            progress_path = Path(root) / "runtime.jsonl"
            cancel_path = Path(f"{progress_path}.cancel.json")
            cancel_path.write_text(json.dumps({"reason": "Early escape"}), encoding="utf-8")

            bridge = RuntimeBridge(str(progress_path))

            self.assertEqual(bridge.cancel_reason(), "Early escape")
            bridge.cleanup()

    async def test_coder_write_waits_for_typed_approval(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            target = root_path / "demo.txt"
            target.write_text("before\n", encoding="utf-8")
            bridge = RuntimeBridge(str(root_path / "runtime.jsonl"))
            agent = coder_module.create_coder_agent(
                workspace=root,
                approval_handler=bridge.approval_handler,
                transport="http",
            )
            context = RunContext(session_id="session-test", workspace_uri=root_path.as_uri())

            running = asyncio.create_task(
                agent.call_tool_in_context(
                    "generate_unified_diff",
                    context,
                    path="demo.txt",
                    updated_content="after\n",
                    original_content="before\n",
                )
            )
            request = await self._wait_for_request(bridge)
            self.assertEqual(target.read_text(encoding="utf-8"), "before\n")
            self.assertEqual(request["action"]["capabilities"], ["workspace.write"])
            self.assertEqual(request["action"]["artifacts"][0]["media_type"], "text/x-diff")

            self._write_decision(bridge, request["request_id"], approved=True)
            result = await asyncio.wait_for(running, timeout=3)
            self.assertTrue(result["success"])
            self.assertEqual(target.read_text(encoding="utf-8"), "after\n")
            bridge.cleanup()

    async def test_denied_coder_write_never_executes(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            target = root_path / "demo.txt"
            target.write_text("before\n", encoding="utf-8")
            bridge = RuntimeBridge(str(root_path / "runtime.jsonl"))
            agent = coder_module.create_coder_agent(
                workspace=root,
                approval_handler=bridge.approval_handler,
                transport="http",
            )
            context = RunContext(session_id="session-test", workspace_uri=root_path.as_uri())

            running = asyncio.create_task(
                agent.call_tool_in_context(
                    "generate_unified_diff",
                    context,
                    path="demo.txt",
                    updated_content="after\n",
                    original_content="before\n",
                )
            )
            request = await self._wait_for_request(bridge)
            self._write_decision(bridge, request["request_id"], approved=False)
            with self.assertRaises(ActionDeniedError):
                await asyncio.wait_for(running, timeout=3)
            self.assertEqual(target.read_text(encoding="utf-8"), "before\n")
            bridge.cleanup()

    async def test_cancel_signal_uses_protolink_client_control_plane(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            bridge = RuntimeBridge(str(Path(root) / "runtime.jsonl"))
            bridge.cancel_path.write_text(
                json.dumps({"reason": "Canceled from test"}),
                encoding="utf-8",
            )
            calls: list[dict[str, str]] = []

            class Client:
                async def cancel_task(self, **kwargs):
                    calls.append(kwargs)

            events: list[str] = []
            await asyncio.wait_for(
                _monitor_cancellation(
                    bridge=bridge,
                    client=Client(),
                    agent_url="runtime://architect",
                    task_id="task_123",
                    emit=events.append,
                ),
                timeout=1,
            )
            self.assertEqual(calls[0]["task_id"], "task_123")
            self.assertEqual(calls[0]["reason"], "Canceled from test")
            self.assertEqual(calls[0]["metadata"]["requested_by"], "protoagent-tui")
            self.assertIn("Cancellation accepted", events[0])
            bridge.cleanup()

    async def test_cancel_signal_prefers_the_in_process_agent(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            bridge = RuntimeBridge(str(Path(root) / "runtime.jsonl"))
            bridge.cancel_path.write_text(
                json.dumps({"reason": "Canceled locally"}),
                encoding="utf-8",
            )
            calls: list[tuple[str, str | None]] = []

            class Agent:
                async def cancel_task(self, request):
                    calls.append((request.id, request.reason))

            class Client:
                async def cancel_task(self, **_kwargs):
                    raise AssertionError("remote client should not be used for an in-process agent")

            events: list[str] = []
            await asyncio.wait_for(
                _monitor_cancellation(
                    bridge=bridge,
                    client=Client(),
                    agent=Agent(),
                    agent_url="runtime://architect",
                    task_id="task_local",
                    emit=events.append,
                ),
                timeout=1,
            )
            self.assertEqual(calls, [("task_local", "Canceled locally")])
            self.assertIn("Cancellation accepted", events[0])
            bridge.cleanup()

    async def test_canceled_stream_does_not_echo_the_original_prompt_as_answer(self) -> None:
        from protolink import RunContext, RunRecorder, Task
        from protolink.core.events import TaskStatusUpdateEvent

        task = Task.create_infer(prompt="original prompt")
        task.begin()
        task.cancel("Stopped by test")
        context = RunContext(session_id="session-test").cancel("Stopped by test")
        context.attach_to_task(task)
        event = TaskStatusUpdateEvent(
            task_id=task.id,
            previous_state="working",
            new_state="canceled",
            final=True,
            metadata={"task": task.to_dict()},
        )

        class Client:
            async def send_task_streaming(self, **_kwargs):
                yield event

        bridge = RuntimeBridge(None)
        result = await _send_task_streaming(
            client=Client(),
            agent_url="runtime://architect",
            task=task,
            context=context,
            recorder=RunRecorder(context=context),
            events=[],
            bridge=bridge,
        )

        self.assertEqual(result["status"], "canceled")
        self.assertIsNone(result["content"])

    async def test_streaming_trace_does_not_store_token_chunks(self) -> None:
        from protolink import RunContext, RunRecorder, RunReplay, Task, assert_run_events
        from protolink.core.events import TaskLLMStreamEvent

        task = Task.create_infer(prompt="hello")
        context = RunContext(session_id="session-test")
        recorder = RunRecorder(context=context)

        class Client:
            async def send_task_streaming(self, **_kwargs):
                yield TaskLLMStreamEvent(
                    task_id=task.id,
                    agent_name="architect",
                    llm_event_type="context_prepared",
                    step=1,
                    metadata={
                        "manifest": {
                            "total_estimated_tokens": 128,
                            "context_window": 8192,
                        }
                    },
                )
                yield TaskLLMStreamEvent(
                    task_id=task.id,
                    agent_name="architect",
                    llm_event_type="llm_chunk",
                    step=1,
                    content="hello",
                )
                yield TaskLLMStreamEvent(
                    task_id=task.id,
                    agent_name="architect",
                    llm_event_type="llm_final",
                    step=1,
                    content="done",
                    final=True,
                )

        result = await _send_task_streaming(
            client=Client(),
            agent_url="runtime://architect",
            task=task,
            context=context,
            recorder=recorder,
            events=[],
            bridge=RuntimeBridge(None),
        )

        self.assertEqual(result["content"], "done")
        event_payloads = [event.payload.get("llm_event_type") for event in recorder.events]
        self.assertNotIn("llm_chunk", event_payloads)
        self.assertIn("context_prepared", event_payloads)
        self.assertIn("llm_final", event_payloads)
        report = recorder.to_report(context=context)
        self.assertEqual(report.context_manifests[0]["total_estimated_tokens"], 128)
        assert_run_events(RunReplay(report), ["context.prepared", "llm.stream"])

    def test_preflight_cancel_skips_model_execution(self) -> None:
        from protolink import RunContext, RunRecorder, Task

        with tempfile.TemporaryDirectory() as root:
            bridge = RuntimeBridge(str(Path(root) / "runtime.jsonl"))
            bridge.cancel_path.write_text(
                json.dumps({"reason": "Canceled during startup"}),
                encoding="utf-8",
            )
            task = Task.create_infer(prompt="hello")
            context = RunContext(session_id="session-test")
            context.attach_to_task(task)
            events: list[str] = []
            result = _preflight_cancellation_result(
                bridge=bridge,
                context=context,
                task=task,
                recorder=RunRecorder(context=context),
                events=events,
                emit=events.append,
                provider="ollama",
                model="gemma4:e4b",
            )

            self.assertIsNotNone(result)
            self.assertEqual(result["status"], "canceled")
            self.assertTrue(result["run_context"]["canceled"])
            self.assertEqual(
                result["run_report"]["context"]["cancel_reason"], "Canceled during startup"
            )
            self.assertIn("before model execution", result["events"][-1])
            bridge.cleanup()

    async def _wait_for_request(self, bridge: RuntimeBridge) -> dict:
        for _ in range(100):
            if bridge.request_path and bridge.request_path.exists():
                return json.loads(bridge.request_path.read_text(encoding="utf-8"))
            await asyncio.sleep(0.02)
        self.fail("approval request was not published")

    @staticmethod
    def _write_decision(bridge: RuntimeBridge, request_id: str, *, approved: bool) -> None:
        bridge.decision_path.write_text(
            json.dumps(
                {
                    "approved": approved,
                    "request_id": request_id,
                    "reason": "test decision",
                    "decided_by": "test",
                }
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
