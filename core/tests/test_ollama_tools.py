"""Choose ProtoLink's native provider channel from advertised capabilities."""

import unittest
from unittest.mock import patch

from protoagent_core.llm import _OLLAMA_TOOL_CACHE, llm_kwargs, ollama_native_tools


class OllamaToolsTests(unittest.TestCase):
    def setUp(self):
        _OLLAMA_TOOL_CACHE.clear()
        self.addCleanup(_OLLAMA_TOOL_CACHE.clear)
        self.config = {"id": "ollama", "base_url": "http://provider.invalid", "model": "tiny"}

    def test_advertised_tools_enable_native_channel_with_one_cached_metadata_probe(self):
        with (
            patch("protoagent_core.llm.provider_config", return_value=self.config),
            patch(
                "protoagent_core.models._get_json",
                return_value={"ok": True, "data": {"capabilities": ["completion", "tools"]}},
            ) as fetch,
        ):
            for _ in range(3):
                self.assertTrue(llm_kwargs("ollama")["supports_tool_calling"])
            fetch.assert_called_once_with(
                "http://provider.invalid/api/show", timeout=1.0, payload={"model": "tiny"}
            )

    def test_unknown_unsupported_and_unreachable_models_keep_json_fallback(self):
        for response in (
            {"ok": False},
            {"ok": True, "data": {}},
            {"ok": True, "data": {"capabilities": ["completion"]}},
        ):
            _OLLAMA_TOOL_CACHE.clear()
            with patch("protoagent_core.models._get_json", return_value=response):
                self.assertFalse(ollama_native_tools(self.config, "tiny"))

    def test_explicit_modes_skip_probes_and_invalid_modes_fail(self):
        with patch("protoagent_core.models._get_json") as fetch:
            self.assertTrue(ollama_native_tools({**self.config, "tool_calling": "native"}, "tiny"))
            self.assertFalse(ollama_native_tools({**self.config, "tool_calling": "json"}, "tiny"))
            with patch.dict("os.environ", {"PROTOAGENT_OLLAMA_TOOL_CALLING": "json"}):
                self.assertFalse(ollama_native_tools(self.config, "tiny"))
            with self.assertRaises(ValueError):
                ollama_native_tools({**self.config, "tool_calling": "magic"}, "tiny")
            fetch.assert_not_called()

    def test_probe_cache_is_scoped_to_server_model_and_expires(self):
        with (
            patch(
                "protoagent_core.models._get_json",
                return_value={"ok": True, "data": {"capabilities": ["tools"]}},
            ) as fetch,
            patch("protoagent_core.llm.time.monotonic", side_effect=[0, 0, 0, 601]),
        ):
            for model, url in (
                ("tiny", "http://provider.invalid"),
                ("other", "http://provider.invalid"),
                ("tiny", "http://other.invalid"),
                ("tiny", "http://provider.invalid"),
            ):
                self.assertTrue(ollama_native_tools({**self.config, "base_url": url}, model))
            self.assertEqual(fetch.call_count, 4)
