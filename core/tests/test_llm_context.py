from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from protoagent_core import agent_engine
from protoagent_core import config as config_module
from protoagent_core.config import provider_config, set_context_window
from protoagent_core.llm import (
    DEFAULT_OLLAMA_CONTEXT_WINDOW,
    create_llm_from_config,
    llm_kwargs,
    llm_model_profile,
    ollama_context_window,
    ollama_context_window_details,
)


class OllamaContextTests(unittest.TestCase):
    def test_ollama_request_and_metrics_use_one_context_window(self) -> None:
        config = {
            "id": "ollama",
            "model": "gemma4:e4b",
            "base_url": "http://localhost:11434",
            "api_key": "",
        }
        with patch("protoagent_core.llm.provider_config", return_value=config):
            kwargs = llm_kwargs("ollama")
            profile = llm_model_profile("ollama")

        self.assertEqual(kwargs["model_params"]["num_ctx"], DEFAULT_OLLAMA_CONTEXT_WINDOW)
        self.assertNotIn("metrics_profile", kwargs)
        self.assertEqual(profile.context_window, DEFAULT_OLLAMA_CONTEXT_WINDOW)
        self.assertEqual(profile.provider, "ollama")
        self.assertEqual(profile.model, "gemma4:e4b")
        self.assertTrue(profile.supports_streaming)
        self.assertTrue(profile.supports_tools)
        self.assertTrue(profile.supports_json_schema)
        self.assertEqual(profile.metadata["configured_by"], "protoagent")

    def test_llm_is_configured_through_protolink_metrics_api(self) -> None:
        config = {
            "id": "ollama",
            "model": "gemma4:e4b",
            "base_url": "http://localhost:11434",
            "api_key": "",
        }

        class FakeLLM:
            def __init__(self) -> None:
                self.profile = None

            def configure_metrics(self, profile):
                self.profile = profile
                return self

        llm = FakeLLM()
        with (
            patch("protoagent_core.llm.provider_config", return_value=config),
            patch("protolink.llms.factory.create_llm", return_value=llm),
        ):
            created = create_llm_from_config("ollama")

        self.assertIs(created, llm)
        self.assertEqual(llm.profile.context_window, DEFAULT_OLLAMA_CONTEXT_WINDOW)
        self.assertEqual(llm.profile.model, "gemma4:e4b")

    def test_context_window_honors_protoagent_environment_override(self) -> None:
        with patch.dict(os.environ, {"PROTOAGENT_OLLAMA_NUM_CTX": "16384"}, clear=False):
            self.assertEqual(ollama_context_window({}), 16_384)

    def test_app_context_window_overrides_environment_and_can_reset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_dir = Path(directory)
            config_path = config_dir / "config.json"
            with (
                patch.object(config_module, "CONFIG_DIR", config_dir),
                patch.object(config_module, "CONFIG_PATH", config_path),
                patch.dict(os.environ, {"PROTOAGENT_OLLAMA_NUM_CTX": "4096"}, clear=False),
            ):
                set_context_window("ollama", 16_384)
                details = ollama_context_window_details(provider_config("ollama"))
                self.assertEqual(details["window_tokens"], 16_384)
                self.assertEqual(details["source"], "app config")

                set_context_window("ollama", None)
                self.assertEqual(ollama_context_window(provider_config("ollama")), 4_096)

    def test_context_window_parser_accepts_compact_values(self) -> None:
        self.assertEqual(agent_engine._parse_context_window("16k"), 16_384)
        self.assertEqual(agent_engine._parse_context_window("1m"), 1_048_576)
        self.assertIsNone(agent_engine._parse_context_window("auto"))

    def test_runtime_prompt_preserves_request_inside_one_context_budget(self) -> None:
        tagged = {
            "items": [{"path": "large.py", "kind": "file", "content": "x" * 20_000}],
            "errors": [],
        }
        loom = {
            "workspace": "/tmp/project",
            "items": [{"path": "other.py", "snippet": "z" * 10_000}],
        }
        with patch.dict(os.environ, {"PROTOAGENT_CONTEXT_CHARS": "2000"}, clear=False):
            prompt = agent_engine._runtime_prompt("CURRENT REQUEST", tagged, loom)

        context, request = prompt.rsplit("Current user request:\n", 1)
        self.assertLessEqual(len(context.strip()), 2_000)
        self.assertEqual(request, "CURRENT REQUEST")

    def test_runtime_prompt_leaves_conversation_continuity_to_protolink(self) -> None:
        prompt = agent_engine._runtime_prompt(
            "CURRENT REQUEST",
            {"items": [], "errors": []},
            {
                "workspace": "/tmp/project",
                "items": [{"path": "agent.py", "snippet": "evidence"}],
            },
        )

        self.assertIn("ProtoLink's persistent per-agent history", prompt)
        self.assertNotIn("Previous turn", prompt)


if __name__ == "__main__":
    unittest.main()
