from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from protolink import ActionDeniedError, RunAction, RunContext

import protoagent_core.agents.architect as architect_module
import protoagent_core.agents.coder as coder_module
import protoagent_core.agents.deck as deck_module
import protoagent_core.agents.explorer as explorer_module
import protoagent_core.runtime as runtime_module
from protoagent_core import agent_engine
from protoagent_core import config as config_module
from protoagent_core.agents.architect import architect_system_prompt
from protoagent_core.agents.common import RUNTIME_SCOPES, create_runtime_auth
from protoagent_core.agents.deck import agent_manifest, create_agent_deck
from protoagent_core.agents.scout import create_scout_agent
from protoagent_core.config import (
    default_config,
    optional_agent_enabled,
    set_optional_agent_enabled,
)


class ScoutTests(unittest.IsolatedAsyncioTestCase):
    def test_default_config_keeps_scout_off(self) -> None:
        config = default_config()

        self.assertFalse(optional_agent_enabled("scout", config))
        self.assertFalse(config["optional_agents"]["scout"]["enabled"])

    def test_toggle_persists_and_pyo3_settings_match_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_dir = Path(directory)
            config_path = config_dir / "config.json"
            with (
                patch.object(config_module, "CONFIG_DIR", config_dir),
                patch.object(config_module, "CONFIG_PATH", config_path),
            ):
                enabled_config = set_optional_agent_enabled("scout", True)
                settings = json.loads(agent_engine.get_agent_settings())

                self.assertTrue(optional_agent_enabled("scout", enabled_config))
                self.assertTrue(settings["scout_enabled"])
                self.assertIn("prompt_profile", settings)
                self.assertIn("architecture", settings)
                agents = {agent["name"]: agent for agent in settings["agents"]}
                self.assertTrue(agents["scout"]["enabled"])
                self.assertTrue(agents["scout"]["optional"])

                disabled = json.loads(agent_engine.configure_optional_agent("scout", False))
                self.assertFalse(disabled["scout_enabled"])
                self.assertFalse(
                    json.loads(config_path.read_text(encoding="utf-8"))["optional_agents"]["scout"][
                        "enabled"
                    ]
                )

    def test_unsupported_optional_agent_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "scout"):
            optional_agent_enabled("browser", default_config())

    def test_manifest_always_describes_optional_scout(self) -> None:
        disabled = agent_manifest(
            {"resolved": "small", "label": "Small local model"},
            scout_enabled=False,
        )
        enabled = agent_manifest(
            {"resolved": "small", "label": "Small local model"},
            scout_enabled=True,
        )

        disabled_agents = {agent["name"]: agent for agent in disabled["agents"]}
        self.assertFalse(disabled_agents["scout"]["enabled"])
        self.assertTrue(disabled_agents["scout"]["optional"])
        self.assertEqual(disabled_agents["scout"]["tools"], ["web_search", "fetch_url"])
        self.assertEqual(disabled_agents["scout"]["prompt_profile"], "not-applicable")
        self.assertEqual(
            disabled_agents["scout"]["prompt_profile_label"],
            "Tool-only (no LLM)",
        )
        self.assertNotIn("scout", disabled["architecture"]["stateless"])
        self.assertIn("scout", enabled["architecture"]["stateless"])

    def test_scout_uses_first_party_tools_without_an_llm_or_durable_state(self) -> None:
        agent = create_scout_agent(transport="runtime", prompt_profile="small")

        self.assertIsNone(agent.llm)
        self.assertEqual(agent.storage.__class__.__name__, "InMemoryStorage")
        self.assertEqual(list(agent.tools), ["web_search", "fetch_url"])
        self.assertEqual(
            [getattr(tool, "_protolink_builtin_id", "") for tool in agent.tools.values()],
            ["web_search", "fetch_url"],
        )
        self.assertTrue(
            all(tuple(tool.capabilities) == ("network.read",) for tool in agent.tools.values())
        )
        self.assertEqual([skill.id for skill in agent.card.skills], ["web_search", "fetch_url"])
        self.assertFalse(agent.card.capabilities.delegation)
        self.assertTrue(agent.card.capabilities.tool_calling)
        self.assertFalse(agent.card.capabilities.multi_step_reasoning)

    async def test_scout_policy_allows_only_network_read(self) -> None:
        agent = create_scout_agent(transport="runtime")
        network_action = RunAction(
            kind="network.read",
            name="web_search",
            capabilities=frozenset({"network.read"}),
        )
        authorization = await agent.authorize_action(
            network_action,
            RunContext(session_id="scout-test"),
        )
        self.assertEqual(authorization.action.name, "web_search")

        write_action = RunAction(
            kind="workspace.write",
            name="write_file",
            capabilities=frozenset({"workspace.write"}),
        )
        with self.assertRaises(ActionDeniedError):
            await agent.authorize_action(write_action, RunContext(session_id="scout-test"))

    def test_deck_constructs_scout_only_when_enabled(self) -> None:
        with (
            patch.object(architect_module, "create_selected_llm", return_value=None),
            patch.object(coder_module, "create_selected_llm", return_value=None),
            patch.object(explorer_module, "create_selected_llm", return_value=None),
            patch.object(deck_module, "create_scout_agent", wraps=create_scout_agent) as factory,
        ):
            disabled = create_agent_deck(workspace=".", transport="runtime")
            self.assertNotIn("scout", disabled)
            factory.assert_not_called()

            enabled = create_agent_deck(
                workspace=".",
                transport="runtime",
                scout_enabled=True,
            )

        self.assertEqual(list(enabled), ["explorer", "coder", "scout", "architect"])
        factory.assert_called_once()
        self.assertIsNone(enabled["scout"].llm)

    def test_architect_prompt_states_scout_availability_explicitly(self) -> None:
        self.assertIn(
            "enabled and registered",
            architect_system_prompt(scout_enabled=True),
        )
        disabled = architect_system_prompt(scout_enabled=False)
        self.assertIn("disabled and not registered", disabled)
        self.assertIn("Do not delegate to `scout`", disabled)

    def test_runtime_auth_scopes_include_network_read(self) -> None:
        auth = create_runtime_auth()
        self.assertIn("network.read", RUNTIME_SCOPES)
        self.assertEqual(auth.authenticator.valid_keys[auth.credentials], list(RUNTIME_SCOPES))

    def test_selected_model_passes_persisted_scout_toggle_to_runtime(self) -> None:
        config = default_config()
        config["providers"]["ollama"]["model"] = "test-model"
        config["optional_agents"]["scout"]["enabled"] = True
        run_deck = AsyncMock(return_value={"answer": "done"})
        with (
            patch.object(runtime_module, "load_config", return_value=config),
            patch.object(
                runtime_module,
                "provider_config",
                return_value={"id": "ollama", "model": "test-model"},
            ),
            patch.object(
                runtime_module,
                "prompt_profile_status",
                return_value={
                    "configured": "auto",
                    "resolved": "small",
                    "label": "Small local model",
                },
            ),
            patch.object(runtime_module, "_run_agent_deck", run_deck),
        ):
            runtime_module.run_selected_model("hello", workspace=".")

        self.assertTrue(run_deck.await_args.kwargs["scout_enabled"])


if __name__ == "__main__":
    unittest.main()
