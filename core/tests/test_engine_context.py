"""Application context hooks with actual ProtoLink actions and receipts."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from protolink import Agent, CapabilityPolicy, RunBudget, RunHandle, SubagentLimits, Task
from protolink.llms.factory import create_llm
from protolink.state.conversation import ConversationState
from protolink.storage import SQLiteStorage

from protoagent_core.request_budget import configure_agent_context


class EngineContextTests(unittest.IsolatedAsyncioTestCase):
    def agent(self, responses=(), callback=None, **kwargs):
        model = create_llm("mock", sequential_responses=list(responses), response_callback=callback)
        return Agent(
            name="context-test",
            llm=model,
            system_prompt="Follow workspace policy.",
            verbosity=0,
            **kwargs,
        )

    async def test_hook_injects_current_record_without_replacing_acquisition(self):
        seen = []

        def model(history, prompt):
            seen.append(str(history.to_list()))
            return {"type": "final", "content": "answer"}

        agent = self.agent(callback=model)
        sync = agent.llm.call_action.__func__
        stream = agent.llm.call_action_stream.__func__
        record = SimpleNamespace(snapshot=lambda: {"objective": "Fix empty input", "criteria": []})
        configure_agent_context(agent, record, fallback_window=2048, compact_protocol=True)
        result = await RunHandle.start(agent, Task.create_infer("Current request")).result()
        self.assertEqual(result.status, "completed", result.error)
        self.assertIn("Fix empty input", seen[0])
        self.assertIn("Current request", seen[0])
        self.assertIs(agent.llm.call_action.__func__, sync)
        self.assertIs(agent.llm.call_action_stream.__func__, stream)
        self.assertTrue(agent.execution_version.endswith(":protoagent-context-2"))
        self.assertEqual(agent.llm.protoagent_context_admission["request_count"], 1)

    async def test_tool_offload_is_retrievable_and_keeps_full_receipt(self):
        await self.exercise_offload(delegated=False)

    async def test_old_malformed_answer_does_not_teach_the_model_a_broken_protocol(self):
        seen = []
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        storage = SQLiteStorage(db_path=str(Path(temp.name) / "history.sqlite"))
        agent = self.agent(
            callback=lambda history, prompt: (
                seen.append(history.to_list()) or {"type": "final", "content": "An orchard ledger."}
            ),
            storage=storage,
            state=["conversation"],
        )
        broken = {
            "tool_call": {"name": "explorer", "args": {"tool": "read_file", "path": "README.md"}}
        }
        executed = {
            "type": "agent_call",
            "agent": "explorer",
            "action": "tool_call",
            "tool": "read_file",
            "args": {"path": "README.md"},
        }
        agent.llm.history.add_user("Old project question")
        agent.llm.history.add_assistant(json.dumps(broken))
        agent.llm.history.add_assistant(
            json.dumps({"type": "final", "content": json.dumps(broken)})
        )
        agent.llm.history.add_assistant(json.dumps(executed))
        ConversationState(storage).save_history("old-bad", agent.llm.history)
        configure_agent_context(agent, fallback_window=8192, compact_protocol=True)
        result = await RunHandle.start(
            agent, Task.create_infer("What's this project about?", session_id="old-bad")
        ).result()
        self.assertEqual(result.status, "completed", result.error)
        contents = [message["content"] for message in seen[0]]
        self.assertNotIn(json.dumps(broken), contents)
        self.assertNotIn(json.dumps({"type": "final", "content": json.dumps(broken)}), contents)
        self.assertIn(
            json.dumps(executed), contents, "Executed action history must retain its correlation"
        )

    async def test_child_offload_is_retrievable_and_keeps_full_receipt(self):
        await self.exercise_offload(delegated=True)

    async def exercise_offload(self, delegated):
        calls, observations, pages = [], [], []
        original = {"source": "source " * 4000}

        def model(history, prompt):
            calls.append(1)
            if len(calls) == 1:
                return (
                    {
                        "type": "agent_call",
                        "agent": "explorer",
                        "action": "tool_call",
                        "tool": "large_result",
                        "args": {},
                    }
                    if delegated
                    else {"type": "tool_call", "tool": "large_result", "args": {}}
                )
            if len(calls) == 2:
                data = next(
                    json.loads(message["content"])
                    for message in history.to_list()
                    if message["content"].startswith(
                        '{"type": "agent_result"' if delegated else '{"type": "tool_result"'
                    )
                )
                observation = data["result"]
                observations.append(observation)
                return {
                    "type": "tool_call",
                    "tool": "read_context_artifact",
                    "args": {
                        "artifact_id": observation["context_artifact"],
                        "offset": 40,
                        "max_chars": 128,
                    },
                }
            page = next(
                json.loads(message["content"])["result"]
                for message in history.to_list()
                if message["content"].startswith('{"type": "tool_result"')
                and json.loads(message["content"]).get("tool") == "read_context_artifact"
            )
            pages.append(page)
            return {"type": "final", "content": "Read the requested slice"}

        child = Agent(name="explorer", verbosity=0)
        owner = child if delegated else self.agent(callback=model)

        @owner.tool()
        def large_result() -> dict:
            return original

        async def execute(execution):
            return original

        owner.tools["large_result"].execute_authorized = execute

        agent = (
            self.agent(
                callback=model, subagents=[child], subagent_limits=SubagentLimits(max_concurrency=1)
            )
            if delegated
            else owner
        )
        configure_agent_context(agent, fallback_window=4096, compact_protocol=True)
        result = await RunHandle.start(agent, Task.create_infer("Inspect source")).result()
        self.assertEqual(result.status, "completed", result.error)
        self.assertEqual(len(calls), 3)
        self.assertLessEqual(len(observations[0]["preview"]), 3000)
        artifacts = next(iter(agent.context_artifacts.sessions.values()))
        stored = artifacts[observations[0]["context_artifact"]]
        self.assertEqual(observations[0]["stored_chars"], len(stored))
        self.assertIn(json.dumps(original), stored)
        receipts = [event for event in result.report.events if event.type == "action.completed"]
        self.assertTrue(any(event.payload.get("result") == original for event in receipts))
        self.assertEqual(pages[0]["text"], stored[40:168])

    async def test_large_user_intent_fails_without_silent_truncation(self):
        agent = self.agent([{"type": "final", "content": "should not run"}])
        configure_agent_context(agent, fallback_window=2048, compact_protocol=True)
        result = await RunHandle.start(agent, Task.create_infer("intent " * 10000)).result()
        self.assertEqual(result.status, "failed")
        self.assertIn("Protected model input", str(result.error))
        self.assertEqual(agent.llm._current_seq_idx, 0)

    async def test_old_complete_turn_is_pruned_and_current_task_record_survives(self):
        inputs = []

        def model(history, prompt):
            inputs.append(str(history.to_list()))
            return {
                "type": "final",
                "content": "old summary " * 1000 if len(inputs) == 1 else "Current answer",
            }

        agent = self.agent(callback=model, state=["conversation"])
        record = SimpleNamespace(snapshot=lambda: {"objective": "Keep the required criteria"})
        configure_agent_context(agent, record, fallback_window=2048, compact_protocol=True)
        first = await RunHandle.start(
            agent, Task.create_infer("FIRST_OLD_REQUEST", session_id="shared")
        ).result()
        self.assertEqual(first.status, "completed", first.error)
        second = await RunHandle.start(
            agent, Task.create_infer("CURRENT_REQUEST", session_id="shared")
        ).result()
        self.assertEqual(second.status, "completed", second.error)
        self.assertNotIn("FIRST_OLD_REQUEST", inputs[1])
        self.assertIn("CURRENT_REQUEST", inputs[1])
        self.assertIn("Keep the required criteria", inputs[1])
        self.assertGreater(agent.llm.protoagent_context_admission["evicted_messages"], 0)

    async def test_large_initial_evidence_can_be_replaced_while_intent_survives(self):
        seen = []

        def model(history, prompt):
            seen.append(str(history.to_list()))
            return {"type": "final", "content": "answer"}

        agent = self.agent(callback=model)
        configure_agent_context(agent, fallback_window=2048, compact_protocol=True)
        result = await RunHandle.start(
            agent,
            Task.create_infer(
                "source " * 10000 + "\n\nCurrent user request:\nPreserve my entire request."
            ),
        ).result()
        self.assertEqual(result.status, "completed", result.error)
        self.assertIn("Preserve my entire request.", seen[0])
        self.assertTrue(agent.llm.protoagent_context_admission["reduced_initial_evidence"])

    async def test_parent_deny_is_enforced_for_local_children(self):
        calls = []
        child = Agent(name="coder", verbosity=0)

        @child.tool(capabilities=["filesystem.write"])
        def write() -> str:
            calls.append(1)
            return "effect"

        agent = self.agent(
            [
                {
                    "type": "agent_call",
                    "agent": "coder",
                    "action": "tool_call",
                    "tool": "write",
                    "args": {},
                }
            ],
            subagents=[child],
            policy=CapabilityPolicy({"agent.delegate": "allow", "filesystem.write": "deny"}),
        )
        configure_agent_context(agent, fallback_window=4096, compact_protocol=True)
        result = await RunHandle.start(agent, Task.create_infer("Try the denied write")).result()
        self.assertEqual(result.status, "failed")
        self.assertEqual(calls, [])

    async def test_root_tool_budget_cannot_be_reset_by_a_new_child(self):
        calls = []
        child = Agent(name="explorer", verbosity=0)

        @child.tool(capabilities=["workspace.read"])
        def read(value: str) -> str:
            calls.append(value)
            return value

        responses = [
            {
                "type": "agent_call",
                "agent": "explorer",
                "action": "tool_call",
                "tool": "read",
                "args": {"value": value},
            }
            for value in ("first", "second")
        ]
        agent = self.agent(
            responses, subagents=[child], subagent_limits=SubagentLimits(max_concurrency=1)
        )
        configure_agent_context(agent, fallback_window=4096, compact_protocol=True)
        result = await RunHandle.start(
            agent, Task.create_infer("Read twice", budget=RunBudget(max_tool_calls=1))
        ).result()
        self.assertEqual(result.status, "failed")
        self.assertEqual(calls, ["first"])
        self.assertIn("budget", str(result.error).lower())

    async def test_child_count_limit_stops_further_dispatch(self):
        calls = []
        child = Agent(name="explorer", verbosity=0)

        @child.tool()
        def read(value: str) -> str:
            calls.append(value)
            return value

        agent = self.agent(
            [
                {
                    "type": "agent_call",
                    "agent": "explorer",
                    "action": "tool_call",
                    "tool": "read",
                    "args": {"value": value},
                }
                for value in ("one", "two")
            ],
            subagents=[child],
            subagent_limits=SubagentLimits(max_children=1),
        )
        configure_agent_context(agent, fallback_window=4096, compact_protocol=True)
        result = await RunHandle.start(agent, Task.create_infer("Read twice")).result()
        self.assertEqual(result.status, "failed")
        self.assertEqual(calls, ["one"])

    async def test_repeated_worker_assignments_use_independent_conversations(self):
        histories = []

        def worker(history, prompt):
            histories.append(str(history.to_list()))
            return {"type": "final", "content": "evidence"}

        child = Agent(
            name="explorer", llm=create_llm("mock", response_callback=worker), verbosity=0
        )
        agent = self.agent(
            [
                {"type": "agent_call", "agent": "explorer", "action": "infer", "prompt": text}
                for text in ("FIRST_ASSIGNMENT", "SECOND_ASSIGNMENT")
            ]
            + [{"type": "final", "content": "Done"}],
            subagents=[child],
            subagent_limits=SubagentLimits(max_concurrency=1),
        )
        configure_agent_context(agent, fallback_window=4096, compact_protocol=True)
        result = await RunHandle.start(agent, Task.create_infer("Two assignments")).result()
        self.assertEqual(result.status, "completed", result.error)
        self.assertEqual(len(histories), 2)
        self.assertNotIn("FIRST_ASSIGNMENT", histories[1])
        self.assertIn("SECOND_ASSIGNMENT", histories[1])
