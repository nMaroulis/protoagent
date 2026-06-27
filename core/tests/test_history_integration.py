from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from protolink import Agent, CapabilityPolicy, LLMModelProfile
from protolink.llms.history import ConversationHistory
from protolink.llms.mock_client import MockLLM
from protolink.state.conversation import ConversationState
from protolink.storage import SQLiteStorage

from protoagent_core.history import (
    AGENT_NAMES,
    compact_agent_histories_for_run,
    compact_saved_histories,
    describe_saved_histories,
    persist_architect_turn,
    reset_saved_histories,
)


class ProtoLinkHistoryIntegrationTests(unittest.TestCase):
    def test_run_boundary_compacts_persisted_history_from_model_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = SQLiteStorage(
                db_path=str(Path(directory) / "history.sqlite"),
                table_name="agent_state",
                namespace="protoagent-architect",
            )
            state = ConversationState(storage)
            history = _large_history()
            original_messages = len(history)
            state.save_history("session-test", history)
            llm = MockLLM()
            llm.configure_metrics(LLMModelProfile(context_window=2_048, provider="mock", model="mock-gpt"))
            agent = _test_agent("architect", storage, llm)

            reports = asyncio.run(compact_agent_histories_for_run([agent], "session-test"))

            self.assertEqual(len(reports), 1)
            self.assertTrue(reports[0]["changed"])
            self.assertGreater(reports[0]["removed_messages"], 0)
            self.assertEqual(reports[0]["state_result"]["operation"], "compact")
            compacted = state.get_history("session-test")
            self.assertLess(len(compacted), original_messages)
            self.assertEqual(compacted.messages[0]["role"], "system")

    def test_explicit_compaction_and_reset_cover_the_full_agent_deck(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storages = {
                name: SQLiteStorage(
                    db_path=str(Path(directory) / "history.sqlite"),
                    table_name="agent_state",
                    namespace=f"protoagent-{name}",
                )
                for name in AGENT_NAMES
            }
            for storage in storages.values():
                ConversationState(storage).save_history("session-test", _large_history())
            agents = [(name, _test_agent(name, storage, MockLLM())) for name, storage in storages.items()]

            with (
                patch(
                    "protoagent_core.history.conversation_storage",
                    side_effect=lambda name: storages[name],
                ),
                patch(
                    "protoagent_core.history._compaction_agents",
                    return_value=agents,
                ),
            ):
                report = compact_saved_histories(
                    "session-test",
                    "mock",
                    "mock-gpt",
                    strategy="recent",
                    limit=5,
                )
                reset = reset_saved_histories("session-test")

            self.assertTrue(report["found"])
            self.assertGreater(report["removed_messages"], 0)
            self.assertEqual(report["state_results"][0]["operation"], "compact")
            self.assertEqual(set(reset["cleared_agents"]), set(AGENT_NAMES))
            for storage in storages.values():
                self.assertNotIn("session-test", ConversationState(storage).to_dict())

    def test_describe_saved_histories_reports_model_facing_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storages = {
                name: SQLiteStorage(
                    db_path=str(Path(directory) / "history.sqlite"),
                    table_name="agent_state",
                    namespace=f"protoagent-{name}",
                )
                for name in AGENT_NAMES
            }
            ConversationState(storages["architect"]).save_history("session-test", _large_history())

            with patch(
                "protoagent_core.history.conversation_storage",
                side_effect=lambda name: storages[name],
            ):
                report = describe_saved_histories("session-test", recent_messages=2)

            architect = next(item for item in report["agents"] if item["agent"] == "architect")
            explorer = next(item for item in report["agents"] if item["agent"] == "explorer")
            self.assertTrue(report["found"])
            self.assertTrue(architect["found"])
            self.assertGreater(architect["message_count"], 0)
            self.assertGreater(architect["estimated_tokens"], 0)
            self.assertEqual(len(architect["recent"]), 2)
            self.assertEqual(architect["state_result"]["operation"], "describe")
            self.assertFalse(explorer["found"])

    def test_persist_architect_turn_bootstraps_missing_top_level_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = SQLiteStorage(
                db_path=str(Path(directory) / "history.sqlite"),
                table_name="agent_state",
                namespace="protoagent-architect",
            )

            with patch(
                "protoagent_core.history.conversation_storage",
                side_effect=lambda name: storage if name == "architect" else None,
            ):
                first = persist_architect_turn(
                    "session-test",
                    workspace=directory,
                    user_prompt="hi",
                    assistant_answer="hello back",
                )
                second = persist_architect_turn(
                    "session-test",
                    workspace=directory,
                    user_prompt="hi",
                    assistant_answer="hello back",
                )

            messages = ConversationState(storage).to_dict()["session-test"]
            self.assertTrue(first["changed"])
            self.assertFalse(second["changed"])
            self.assertEqual(messages[-2]["role"], "user")
            self.assertEqual(messages[-2]["content"], "hi")
            self.assertEqual(messages[-1]["role"], "assistant")
            self.assertEqual(messages[-1]["content"], "hello back")


def _test_agent(name: str, storage: SQLiteStorage, llm: MockLLM | None = None) -> Agent:
    if llm is not None:
        llm.configure_metrics(LLMModelProfile(context_window=2_048, provider="mock", model="mock-gpt"))
    return Agent(
        card={"name": name, "description": f"{name} test agent", "url": f"runtime://{name}"},
        transport=None,
        llm=llm,
        storage=storage,
        state=["conversation"],
        policy=CapabilityPolicy(
            {
                "llm.history.compact": "allow",
                "state.compact": "allow",
                "state.describe": "allow",
                "state.reset": "allow",
            },
            default_effect="deny",
        ),
        verbosity=0,
    )


def _large_history() -> ConversationHistory:
    history = ConversationHistory(system_prompt="system prompt")
    for index in range(12):
        history.add_user(f"question {index}: " + "x" * 240)
        history.add_assistant(f"answer {index}: " + "y" * 240)
    return history


if __name__ == "__main__":
    unittest.main()
