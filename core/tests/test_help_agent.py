from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest.mock import patch

from protolink.llms.mock_client import MockLLM

from protoagent_core.help_agent import answer_help_question, command_reference


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
            return "Use /config to view settings; use /model to change models."

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
            result = answer_help_question("what is the config command?")

        self.assertEqual(result["agent"], "guide")
        self.assertEqual(result["provider"], "mock")
        self.assertEqual(result["model"], "mock-gpt")
        self.assertIn("/model", result["answer"])
        self.assertIn("/config", result["answer"])
        self.assertIn("`/config` opens the redacted configuration panel", seen_prompt["system"])
        self.assertIn("`proto-cli config`", seen_prompt["system"])
        self.assertIn("Both are read-only", seen_prompt["system"])
        self.assertIn("TUI slash commands:", seen_prompt["system"])
        self.assertIn("Shell commands (proto-cli ...):", seen_prompt["system"])
        self.assertIn("/agents profile [auto|small|medium|large|api]", seen_prompt["system"])
        self.assertIn("/agents scout on", seen_prompt["system"])
        self.assertIn("BRAVE_SEARCH_API_KEY", seen_prompt["system"])
        self.assertIn("Active provider: mock", seen_prompt["user"])
        self.assertIn("Active model: mock-gpt", seen_prompt["user"])
        self.assertIn("Prompt profile: auto configured, medium resolved", seen_prompt["user"])
        self.assertIn("Optional Scout: disabled", seen_prompt["user"])
        self.assertIn("Persistent context memory: on (default)", seen_prompt["user"])
        self.assertIn("User help question:\nwhat is the config command?", seen_prompt["user"])

    def test_guide_catalog_covers_all_top_level_tui_handlers_and_aliases(self):
        source = (Path(__file__).parents[2] / "cli/src/terminal_ui.rs").read_text()
        handler = source.split("async fn handle_command", 1)[1].split(
            "async fn handle_help_command", 1
        )[0]
        commands = set(re.findall(r'"(/[a-z]+)"', handler))
        reference = command_reference()
        for command in commands:
            with self.subTest(command=command):
                self.assertIn(command + ":", reference)


if __name__ == "__main__":
    unittest.main()
