"""Offline setup stays fast and does not masquerade as a completed model run."""

import json
import unittest
from unittest.mock import patch

from protoagent_core import agent_engine, help_agent, models


class ModelReadinessTests(unittest.TestCase):
    def check(self, provider="ollama", model="small", response=None, key=""):
        config = {"active_provider": provider, "providers": {provider: {"model": model}}}
        with (
            patch.object(models, "visible_config", return_value=config),
            patch.object(
                models,
                "provider_config",
                return_value={"base_url": "http://127.0.0.1:9", "api_key": key},
            ),
            patch.object(models, "_get_json", return_value=response or {"ok": False}) as get,
        ):
            return models.model_startup_problem(), get

    def test_missing_model_needs_input_without_network(self):
        result, get = self.check(model="")
        self.assertEqual(result["status"], "input_required")
        self.assertIn("/model", result["answer"])
        get.assert_not_called()

    def test_offline_local_server_has_a_short_metadata_only_probe(self):
        result, get = self.check()
        self.assertEqual(result["status"], "failed")
        self.assertIn("ollama serve", result["answer"])
        get.assert_called_once_with("http://127.0.0.1:9/api/tags", headers=None, timeout=1.0)

    def test_ollama_requires_an_installed_model_and_accepts_latest_alias(self):
        for names, expected in [([], "input_required"), ([{"name": "small:latest"}], None)]:
            result, _ = self.check(response={"ok": True, "data": {"models": names}})
            self.assertEqual(result["status"] if result else None, expected)

    def test_cloud_setup_checks_key_without_constructing_an_llm_or_inference(self):
        result, get = self.check(provider="openai")
        self.assertEqual(result["status"], "input_required")
        self.assertIn("/key openai", result["answer"])
        get.assert_not_called()
        result, get = self.check(provider="openai", key="fixture-key")
        self.assertIsNone(result)
        get.assert_not_called()

    def test_compatible_chat_server_without_model_list_is_not_rejected(self):
        result, _ = self.check(
            provider="openai-compatible", response={"ok": False, "status_code": 404}
        )
        self.assertIsNone(result)

    def test_missing_gguf_and_invalid_server_metadata_are_reported(self):
        result, get = self.check(provider="llama.cpp-local", model="/nonexistent/model.gguf")
        self.assertEqual(result["status"], "input_required")
        get.assert_not_called()
        result, _ = self.check(response={"ok": True, "data": []})
        self.assertEqual(result["status"], "failed")

    def test_cli_task_rejects_offline_before_indexing_or_model_construction(self):
        problem = {"status": "failed", "answer": "Offline fixture"}
        with (
            patch.object(agent_engine, "model_startup_problem", return_value=problem),
            patch.object(agent_engine, "_context_pack_for_prompt") as weave,
            patch.object(agent_engine, "_model_response") as run,
        ):
            result = json.loads(
                agent_engine.process_prompt(
                    "task", "/tmp", progress_path="/tmp/unused-readiness-progress"
                )
            )
        self.assertEqual(result["status"], "failed")
        weave.assert_not_called()
        run.assert_not_called()

    def test_cli_guide_rejects_offline_before_constructing_an_llm(self):
        problem = {"status": "failed", "answer": "Offline fixture"}
        with (
            patch.object(help_agent, "model_startup_problem", return_value=problem),
            patch.object(help_agent, "create_llm_from_config") as construct,
        ):
            result = help_agent.answer_help_question(
                "how to start?", "/tmp/unused-readiness-progress"
            )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["agent"], "guide")
        construct.assert_not_called()

    def test_inventory_key_validation_does_not_create_native_clients(self):
        with (
            patch.object(models, "_VALIDATION_CACHE", {}),
            patch.object(models, "_get_json", return_value={"ok": True, "data": {}}) as get,
            patch("protoagent_core.llm.create_llm_from_config") as construct,
        ):
            result = models._validate_api_key("openai", "fixture-key")
        self.assertEqual(result["status"], "valid")
        get.assert_called_once()
        construct.assert_not_called()


if __name__ == "__main__":
    unittest.main()
