"""Provider configuration delegates action policies and discovery to ProtoLink."""

import unittest
from unittest.mock import patch

from protolink.llms.server.ollama_client import OllamaLLM

from protoagent_core.llm import create_llm_from_config, llm_kwargs, validate_protolink


class OllamaToolsTests(unittest.TestCase):
    def setUp(self):
        self.config = {"id": "ollama", "base_url": "http://provider.invalid", "model": "tiny:tag"}

    def configured(self, **options):
        with (
            patch("protoagent_core.llm.provider_config", return_value={**self.config, **options}),
            patch.object(OllamaLLM, "validate_connection", return_value=True),
        ):
            return create_llm_from_config("ollama")

    def test_kwargs_select_engine_auto_without_probing_or_setting_legacy_flags(self):
        with (
            patch("protoagent_core.llm.provider_config", return_value=self.config),
            patch.object(OllamaLLM, "discover_tool_calling") as discover,
        ):
            kwargs = llm_kwargs("ollama")
        self.assertEqual(kwargs["tool_calling"], "auto")
        self.assertNotIn("supports_tool_calling", kwargs)
        discover.assert_not_called()

    def test_configured_api_urls_reach_the_native_adapters(self):
        for provider in ("gemini", "anthropic", "openai"):
            with (
                self.subTest(provider=provider),
                patch(
                    "protoagent_core.llm.provider_config",
                    return_value={
                        "model": "fixture-model",
                        "base_url": "https://fixture.invalid/api",
                    },
                ),
            ):
                self.assertEqual(llm_kwargs(provider)["base_url"], "https://fixture.invalid/api")

    def test_native_discovery_cache_refresh_and_unknown_metadata(self):
        model = self.configured()
        with patch.object(OllamaLLM, "discover_tool_calling", return_value=True) as discover:
            for _ in range(3):
                self.assertEqual(model.resolve_tool_calling(), "native")
            discover.assert_called_once()
            model.refresh_capabilities()
            self.assertEqual(model.resolve_tool_calling(), "native")
            self.assertEqual(discover.call_count, 2)
        for capability in (False, None):
            model.refresh_capabilities()
            with patch.object(OllamaLLM, "discover_tool_calling", return_value=capability):
                self.assertEqual(model.resolve_tool_calling(), "json")

    def test_native_metadata_request_keeps_exact_tag_and_authentication(self):
        model = self.configured(api_key="test-key")
        with patch("protolink.llms.server.ollama_client.http.client.HTTPConnection") as connection:
            response = connection.return_value.getresponse.return_value
            response.status = 200
            response.read.return_value = b'{"capabilities":["completion","tools"]}'
            self.assertEqual(model.resolve_tool_calling(), "native")
            request = connection.return_value.request.call_args
            self.assertEqual(request.args, ("POST", "/api/show"))
            self.assertEqual(request.kwargs["body"], '{"model": "tiny:tag"}')
            self.assertEqual(request.kwargs["headers"]["Authorization"], "Bearer test-key")

    def test_explicit_modes_skip_discovery_and_config_overrides_environment(self):
        with patch.object(OllamaLLM, "discover_tool_calling") as discover:
            for mode in ("native", "json"):
                with patch.dict("os.environ", {"PROTOAGENT_OLLAMA_TOOL_CALLING": "json"}):
                    self.assertEqual(
                        self.configured(tool_calling=mode).resolve_tool_calling(), mode
                    )
            with patch.dict("os.environ", {"PROTOAGENT_OLLAMA_TOOL_CALLING": "native"}):
                self.assertEqual(self.configured().resolve_tool_calling(), "native")
            with self.assertRaises(ValueError):
                self.configured(tool_calling="magic")
            discover.assert_not_called()

    def test_correction_policy_stays_out_of_generation_options_and_bounds_router(self):
        for fallbacks in ([], ["backup"]):
            model = self.configured(max_tool_corrections=1, fallback_models=fallbacks)
            self.assertEqual(model.max_tool_corrections, 1)
            candidates = getattr(model, "models", {"selected": model})
            for candidate in candidates.values():
                self.assertEqual(candidate.max_tool_corrections, 1)
                self.assertNotIn("max_tool_corrections", candidate.model_params)
                self.assertNotIn("tool_calling", candidate.model_params)
        for value in (True, -1, "two", 11):
            with self.assertRaises((TypeError, ValueError)):
                self.configured(max_tool_corrections=value)

    def test_other_adapters_keep_defaults_or_take_an_explicit_policy(self):
        for mode in (None, "json"):
            cfg = {"id": "mock", "model": "offline"}
            if mode is not None:
                cfg["tool_calling"] = mode
            with patch("protoagent_core.llm.provider_config", return_value=cfg):
                model = create_llm_from_config("mock")
            self.assertEqual(model.tool_calling, mode)

    def test_readiness_requires_native_rejection_and_capability_apis(self):
        with patch("protolink.ToolRejectedError", None):
            report = validate_protolink()
        self.assertFalse(report["action_validation_ready"])
        self.assertFalse(report["agent_ready"])
        self.assertIn("0.8.2", report["error"])
