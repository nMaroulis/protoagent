from __future__ import annotations

import unittest
from unittest.mock import patch

from protolink.llms.mock_client import MockLLM

from protoagent_core.help_agent import answer_help_question


class GuideHelpAgentTests(unittest.TestCase):
    def test_guide_answers_without_storage_or_registered_tools(self) -> None:
        seen_prompt = {}

        def respond(history, _system_prompt):
            seen_prompt["system"] = str(_system_prompt)
            seen_prompt["user"] = next(
                str(message.get("content", ""))
                for message in reversed(history.messages)
                if message.get("role") == "user"
            )
            return "Use /model to change models."

        with (
            patch(
                "protoagent_core.help_agent.visible_config",
                return_value={
                    "active_provider": "mock",
                    "config_path": "/tmp/protoagent-test/config.json",
                    "providers": {
                        "mock": {
                            "label": "Mock",
                            "model": "mock-gpt",
                            "api_key_set": False,
                        }
                    },
                },
            ),
            patch(
                "protoagent_core.help_agent.create_llm_from_config",
                return_value=MockLLM(response_callback=respond),
            ),
        ):
            result = answer_help_question("how do I change models?")

        self.assertEqual(result["agent"], "guide")
        self.assertEqual(result["provider"], "mock")
        self.assertEqual(result["model"], "mock-gpt")
        self.assertIn("/model", result["answer"])
        self.assertIn("/agents profile [auto|small|medium|large|api]", seen_prompt["system"])
        self.assertIn("Active provider: mock", seen_prompt["user"])
        self.assertIn("Active model: mock-gpt", seen_prompt["user"])
        self.assertIn("Prompt profile: auto configured, medium resolved", seen_prompt["user"])
        self.assertIn("Persistent context memory: on (default)", seen_prompt["user"])
        self.assertIn("User help question:\nhow do I change models?", seen_prompt["user"])


if __name__ == "__main__":
    unittest.main()
