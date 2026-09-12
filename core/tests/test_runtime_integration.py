from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from protolink import ActionDeniedError, RunAction, RunBudget, RunContext

import protoagent_core.agents.architect as architect_module
import protoagent_core.agents.coder as coder_module
import protoagent_core.agents.explorer as explorer_module
from protoagent_core.agents.common import create_runtime_auth
from protoagent_core.agents.deck import create_agent_deck
from protoagent_core.runtime import (
    _run_budget,
    _transport_report,
)


class RuntimeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._config_temp = tempfile.TemporaryDirectory()
        self._config_patch = patch(
            "protoagent_core.config.CONFIG_DIR", Path(self._config_temp.name)
        )
        self._config_patch.start()
        self._create_architect_llm = architect_module.create_selected_llm
        self._create_selected_llm = coder_module.create_selected_llm
        self._create_explorer_llm = explorer_module.create_selected_llm
        architect_module.create_selected_llm = lambda *_args, **_kwargs: None
        coder_module.create_selected_llm = lambda *_args, **_kwargs: None
        explorer_module.create_selected_llm = lambda *_args, **_kwargs: None

    def tearDown(self) -> None:
        self._config_patch.stop()
        self._config_temp.cleanup()
        architect_module.create_selected_llm = self._create_architect_llm
        coder_module.create_selected_llm = self._create_selected_llm
        explorer_module.create_selected_llm = self._create_explorer_llm

    def test_explorer_uses_inferred_json_schema_defaults(self) -> None:
        agent = explorer_module.create_explorer_agent(workspace=".", transport="http")
        schema = agent.tools["search_regex"].input_schema
        self.assertEqual(schema["required"], ["pattern"])
        self.assertEqual(schema["properties"]["path"]["default"], ".")
        self.assertEqual(schema["properties"]["file_filter"]["default"], ".*")

    async def test_explorer_policy_denies_unmatched_capability_by_default(self) -> None:
        agent = explorer_module.create_explorer_agent(workspace=".", transport="http")
        action = RunAction(
            kind="shell.execute",
            name="run_shell",
            capabilities=frozenset({"shell.execute"}),
        )
        with self.assertRaises(ActionDeniedError):
            await agent.authorize_action(action, RunContext(session_id="session-test"))

    async def test_state_compaction_control_plane_is_agent_authorized(self) -> None:
        agent = architect_module.create_architect_agent(workspace=".", transport="http")
        action = RunAction(
            kind="state.compact",
            name="compact_state",
            capabilities=frozenset({"state.compact", "llm.history.compact"}),
        )
        authorization = await agent.authorize_action(action, RunContext(session_id="session-test"))
        self.assertEqual(authorization.action.name, "compact_state")

    def test_explorer_and_coder_are_stateless_workers(self) -> None:
        explorer = explorer_module.create_explorer_agent(workspace=".", transport="http")
        coder = coder_module.create_coder_agent(workspace=".", transport="http")

        self.assertEqual(explorer.storage.__class__.__name__, "InMemoryStorage")
        self.assertEqual(coder.storage.__class__.__name__, "InMemoryStorage")

    def test_agent_deck_uses_shared_protolink_auth(self) -> None:
        auth = create_runtime_auth()
        deck = create_agent_deck(workspace=".", transport="http", auth=auth)

        for agent in deck.values():
            self.assertIs(agent.authenticator, auth.authenticator)
            self.assertEqual(agent.credentials, auth.credentials)
            self.assertIs(agent.transport.authenticator, auth.authenticator)
            self.assertEqual(agent.transport.credentials, auth.credentials)
            self.assertIn("apiKey", agent.card.security_schemes)
            self.assertTrue(agent.transport.config.collect_metrics)
            self.assertTrue(agent.transport.capabilities.networked)
            self.assertEqual(agent.transport.metrics.requests_started, 0)

        report = _transport_report(
            deck,
            deck["architect"].transport,
        )
        self.assertEqual(report["agents"]["architect"]["transport"], "http")
        self.assertTrue(report["agents"]["architect"]["config"]["collect_metrics"])
        self.assertEqual(report["agents"]["coder"]["metrics"]["requests_started"], 0)

    def test_run_budget_uses_protolink_budget_carrier(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "PROTOAGENT_RUN_MAX_STEPS": "12",
                "PROTOAGENT_RUN_MAX_INPUT_TOKENS": "16000",
                "PROTOAGENT_RUN_MAX_OUTPUT_TOKENS": "2048",
                "PROTOAGENT_RUN_MAX_SECONDS": "45",
            },
        ):
            budget = _run_budget("openai", "gpt-test", RunBudget)

        self.assertEqual(budget.max_steps, 12)
        self.assertEqual(budget.max_runtime_seconds, 45.0)
        self.assertEqual(budget.max_input_tokens, 16000)
        self.assertEqual(budget.max_output_tokens, 2048)
        self.assertEqual(budget.metadata["source"], "protoagent-runtime")

    def test_uncertain_cli_response_preserves_outcome_without_marking_provider_valid(self):
        from protoagent_core.agent_engine import _model_response

        with (
            patch(
                "protoagent_core.runtime.run_selected_model",
                return_value={
                    "status": "uncertain",
                    "answer": "Inspect the run before continuing.",
                    "provider": "mock",
                    "model": "scripted",
                },
            ),
            patch("protoagent_core.agent_engine.remember_valid_provider") as remember,
            patch("protoagent_core.agent_engine.build_context_map", return_value={"files": []}),
            patch("protoagent_core.history.persist_architect_turn"),
        ):
            response = _model_response("change a file", ".", 0)
        self.assertEqual(response["status"], "uncertain")
        self.assertNotIn("completed", response["headline"])
        remember.assert_not_called()
