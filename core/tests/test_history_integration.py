from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from protolink import LLMModelProfile
from protolink.llms.history import ConversationHistory
from protolink.llms.mock_client import MockLLM
from protolink.state.conversation import ConversationState
from protolink.storage import SQLiteStorage

from protoagent_core.history import (
    AGENT_NAMES,
    compact_agent_histories_for_run,
    compact_saved_histories,
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
            agent = SimpleNamespace(
                llm=llm,
                storage=storage,
                card=SimpleNamespace(name="architect"),
            )

            reports = compact_agent_histories_for_run([agent], "session-test")

            self.assertEqual(len(reports), 1)
            self.assertTrue(reports[0]["changed"])
            self.assertGreater(reports[0]["removed_messages"], 0)
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

            with (
                patch(
                    "protoagent_core.history.conversation_storage",
                    side_effect=lambda name: storages[name],
                ),
                patch(
                    "protoagent_core.history.create_llm_from_config",
                    side_effect=lambda *_args: MockLLM(),
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
            self.assertEqual(set(reset["cleared_agents"]), set(AGENT_NAMES))
            for storage in storages.values():
                self.assertNotIn("session-test", ConversationState(storage).to_dict())


def _large_history() -> ConversationHistory:
    history = ConversationHistory(system_prompt="system prompt")
    for index in range(12):
        history.add_user(f"question {index}: " + "x" * 240)
        history.add_assistant(f"answer {index}: " + "y" * 240)
    return history


if __name__ == "__main__":
    unittest.main()
