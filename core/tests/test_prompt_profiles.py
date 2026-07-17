from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from protoagent_core import config as config_module
from protoagent_core.config import set_agent_prompt_profile, visible_config
from protoagent_core.prompt_profiles import (
    PROMPT_PROFILES,
    compose_system_prompt,
    infer_prompt_profile,
    prompt_profile_status,
)


class PromptProfileTests(unittest.TestCase):
    def test_every_resolved_profile_has_a_scout_overlay(self) -> None:
        for profile in PROMPT_PROFILES.values():
            self.assertIn("web_search", profile.role_prompt("scout"))

    def test_auto_inference_uses_model_and_provider_capability_hints(self) -> None:
        self.assertEqual(infer_prompt_profile("ollama", "qwen2.5-coder:7b"), "small")
        self.assertEqual(infer_prompt_profile("lmstudio", "qwen3-coder:14b"), "medium")
        self.assertEqual(infer_prompt_profile("ollama", "llama3.3:70b"), "large")
        self.assertEqual(infer_prompt_profile("openai", "gpt-5.2"), "api")
        self.assertEqual(infer_prompt_profile("anthropic", "claude-opus-4.8"), "api")

    def test_explicit_profile_overrides_auto_resolution(self) -> None:
        status = prompt_profile_status(
            {
                "active_provider": "openai",
                "agent_prompt_profile": "small",
                "providers": {"openai": {"model": "gpt-5.2"}},
            }
        )

        self.assertEqual(status["configured"], "small")
        self.assertEqual(status["resolved"], "small")
        self.assertEqual(status["label"], "Small local model")

    def test_compose_system_prompt_attaches_role_specific_overlay(self) -> None:
        prompt = compose_system_prompt(
            "Base architect prompt.",
            "architect",
            provider="ollama",
            model="qwen2.5-coder:7b",
        )

        self.assertIn("Base architect prompt.", prompt)
        self.assertIn("Prompt profile: Small local model.", prompt)
        self.assertIn("Use the exact agent names `explorer` and `coder`.", prompt)
        self.assertIn("Do not reveal hidden chain-of-thought", prompt)

    def test_config_persists_prompt_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_dir = Path(directory)
            config_path = config_dir / "config.json"
            with (
                patch.object(config_module, "CONFIG_DIR", config_dir),
                patch.object(config_module, "CONFIG_PATH", config_path),
            ):
                set_agent_prompt_profile("large")
                config = visible_config()

        self.assertEqual(config["agent_prompt_profile"], "large")

    def test_status_falls_back_to_auto_for_manually_invalid_config(self) -> None:
        status = prompt_profile_status(
            {
                "active_provider": "ollama",
                "agent_prompt_profile": "mystery",
                "providers": {"ollama": {"model": "llama3.1:8b"}},
            }
        )

        self.assertEqual(status["configured"], "auto")
        self.assertEqual(status["resolved"], "small")


if __name__ == "__main__":
    unittest.main()
