"""Explicit native model fallback, with no replay of completed tools."""

import unittest
from unittest.mock import patch

from protolink import Agent, RoutedLLM, RunBudget, RunHandle, Task
from protolink.llms.factory import create_llm

from protoagent_core.llm import create_llm_from_config
from protoagent_core.request_budget import configure_agent_context


class ModelRoutingTests(unittest.IsolatedAsyncioTestCase):
    def configured(self, names):
        cfg = {"id": "mock", "model": "selected", "fallback_models": names}
        with patch("protoagent_core.llm.provider_config", return_value=cfg):
            return create_llm_from_config("mock")

    def test_routing_requires_explicit_unique_bounded_same_provider_models(self):
        model = self.configured([])
        self.assertNotIsInstance(model, RoutedLLM)
        routed = self.configured(["backup"])
        self.assertIsInstance(routed, RoutedLLM)
        self.assertEqual({item.provider for item in routed.models.values()}, {"mock"})
        for names in ("backup", ["selected"], ["backup", "backup"], ["a", "b", "c"], [""]):
            with self.assertRaises(ValueError):
                self.configured(names)

    async def test_transient_failure_after_tool_completion_falls_back_without_replay(self):
        calls, effects = [], []
        primary = create_llm(
            "mock", sequential_responses=[{"type": "tool_call", "tool": "effect", "args": {}}]
        )
        acquire = primary.call

        def unstable(history):
            calls.append(1)
            if len(calls) > 1:
                raise ConnectionError("connection lost before the model reply")
            return acquire(history)

        primary.call = unstable
        backup = create_llm("mock", default_response='{"type":"final","content":"Finished"}')
        model = RoutedLLM({"selected": primary, "backup": backup}, fallbacks=["backup"])
        agent = Agent(name="routing-test", llm=model, verbosity=0)

        @agent.tool()
        def effect() -> str:
            effects.append(1)
            return "committed result"

        configure_agent_context(agent, fallback_window=4096, compact_protocol=True)
        result = await RunHandle.start(
            agent, Task.create_infer("Perform one action", budget=RunBudget(max_llm_calls=3))
        ).result()
        self.assertEqual(result.status, "completed", result.error)
        self.assertEqual(effects, [1])
        self.assertEqual(calls, [1, 1])
