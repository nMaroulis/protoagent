"""Exercise real SDK requests through ProtoLink without contacting providers."""

import json
import unittest
from unittest.mock import patch

import anthropic
import httpx
import openai
from protolink.llms.api.anthropic_client import AnthropicLLM
from protolink.llms.api.openai_client import OpenAILLM
from protolink.llms.history import ConversationHistory


class ProviderSDKCompatibilityTests(unittest.TestCase):
    def test_openai_sdk_accepts_the_engine_request_and_preserves_the_answer(self):
        self.check_adapter(OpenAILLM, openai.OpenAI, "openai")

    def test_anthropic_sdk_accepts_the_engine_generation_parameters(self):
        # Anthropic 1.x removed temperature/top_p; this must fail if a future
        # dependency update loses ProtoLink 0.8.1's compatibility constraint.
        self.check_adapter(AnthropicLLM, anthropic.Anthropic, "anthropic")

    def check_adapter(self, adapter, sdk, provider):
        history = ConversationHistory(system_prompt="Answer the fixture question.")
        history.add_user("Say hello.")
        requests = []

        def respond(request):
            body = json.loads(request.content)
            self.assertEqual(body["model"], "fixture-model")
            self.assertEqual(request.url.host, "sdk-fixture.invalid")
            requests.append(request.url.path)
            if provider == "openai":
                result = {
                    "id": "resp_fixture",
                    "object": "response",
                    "created_at": 0,
                    "model": "fixture-model",
                    "status": "completed",
                    "output": [
                        {
                            "id": "msg_fixture",
                            "type": "message",
                            "role": "assistant",
                            "status": "completed",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "Hello fixture.",
                                    "annotations": [],
                                }
                            ],
                        }
                    ],
                    "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
                }
            else:
                self.assertIn("temperature", body)
                self.assertIn("top_p", body)
                result = {
                    "id": "msg_fixture",
                    "type": "message",
                    "role": "assistant",
                    "model": "fixture-model",
                    "content": [{"type": "text", "text": "Hello fixture."}],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": {"input_tokens": 2, "output_tokens": 3},
                }
            return httpx.Response(200, json=result)

        with patch.object(adapter, "validate_connection", return_value=True):
            llm = adapter(api_key="fixture-only", model="fixture-model")
        llm._client.close()
        with sdk(
            api_key="fixture-only",
            base_url="https://sdk-fixture.invalid/v1",
            http_client=httpx.Client(transport=httpx.MockTransport(respond)),
        ) as client:
            llm._client = client
            self.assertEqual(llm.call_action(history, tools={}).action.content, "Hello fixture.")
        self.assertEqual(len(requests), 1)
