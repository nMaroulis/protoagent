"""A provider's intended action must execute through ProtoLink or fail visibly."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from protolink.llms.metrics import estimate_token_count
from protolink.llms.server.ollama_client import OllamaLLM
from runtime_support import NativeRuntimeCase

from protoagent_core.response_contract import validate_final_answer
from protoagent_core.runtime import _run_agent_deck

UNFINISHED = {"tool_call": {"name": "explorer", "args": {"tool": "read_file", "path": "README.md"}}}


class ActionAnswerTests(NativeRuntimeCase):
    async def run_replies(self, messages, *, native=False):
        requests = []
        replies = iter(messages)

        def respond(request):
            requests.append(json.loads(request.content))
            payload = {"message": next(replies), "done": True}
            return httpx.Response(200, content=json.dumps(payload) + "\n")

        client = httpx.AsyncClient
        with (
            patch.object(OllamaLLM, "validate_connection", return_value=True),
            patch(
                "httpx.AsyncClient",
                side_effect=lambda **kw: client(transport=httpx.MockTransport(respond), **kw),
            ),
            patch.dict("os.environ", {"PROTOAGENT_AGENT_TRANSPORT": "local"}),
            patch("protoagent_core.agents.coder.create_selected_llm", return_value=None),
            patch("protoagent_core.agents.explorer.create_selected_llm", return_value=None),
        ):
            llm = OllamaLLM(
                base_url="http://provider.invalid", model="small", supports_tool_calling=native
            )
            with patch("protoagent_core.agents.architect.create_selected_llm", return_value=llm):
                result = await _run_agent_deck(
                    "What's this project about?",
                    "ollama",
                    "small",
                    str(self.root),
                    None,
                    self.bridge,
                    {"resolved": "small"},
                    tester_enabled=False,
                )
        return result, requests

    async def test_screenshot_wrapper_is_not_a_successful_answer_in_either_mode(self):
        for native in (False, True):
            with self.subTest(native=native):
                result, requests = await self.run_replies(
                    [{"content": json.dumps(UNFINISHED)}], native=native
                )
                self.assertEqual(result["status"], "failed", result["answer"])
                self.assertIn("unfinished tool or worker request", result["answer"])
                self.assertNotIn(json.dumps(UNFINISHED), result["answer"])
                self.assertEqual(len(requests), 1, "A rejected answer must not replay the run")
                self.assertFalse(
                    any(
                        event["payload"].get("llm_event_type") == "llm_final"
                        for event in result["run_events"]
                    )
                )
                self.assertFalse(
                    any(event["type"] == "subagent.started" for event in result["run_events"])
                )

    async def test_native_delegation_reads_readme_and_returns_project_explanation(self):
        (self.root / "README.md").write_text("# TaskFlow\nA small task management service.\n")
        answer = "TaskFlow is a small task management service."
        result, requests = await self.run_replies(
            [
                {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "protolink_call_agent_tool",
                                "arguments": {
                                    "agent": "explorer",
                                    "tool": "read_file",
                                    "args_json": '{"path":"README.md"}',
                                },
                            }
                        }
                    ]
                },
                {"content": answer},
            ],
            native=True,
        )
        self.assertEqual(result["status"], "completed", result["answer"])
        self.assertEqual(result["answer"], answer)
        self.assertEqual(len(requests), 2)
        self.assertNotIn("format", requests[0])
        self.assertTrue(
            any(
                tool["function"]["name"] == "protolink_call_agent_tool"
                for tool in requests[0]["tools"]
            )
        )
        self.assertIn("A small task management service", json.dumps(requests[1]["messages"]))
        self.assertEqual(
            result["context_admission"]["architect"]["schema_tokens"],
            estimate_token_count(requests[0]["tools"]),
            "Small-model admission must also account for native delegation schemas",
        )
        self.assertTrue(
            any(
                event["type"] == "action.completed"
                and event.get("agent_name") == "explorer"
                and event["payload"].get("action", {}).get("name") == "read_file"
                for event in result["run_events"]
            ),
            "A real Explorer execution receipt must back the answer",
        )

    async def test_json_protocol_still_delegates_with_native_parse_correction(self):
        (self.root / "README.md").write_text("A small task management service.\n")
        action = {
            "type": "agent_call",
            "agent": "explorer",
            "action": "tool_call",
            "tool": "read_file",
            "args": {"path": "README.md"},
        }
        result, requests = await self.run_replies(
            [
                {"content": json.dumps({**action, "unsupported": True})},
                {"content": json.dumps(action)},
                {
                    "content": json.dumps(
                        {"type": "final", "content": "A small task management service."}
                    )
                },
            ]
        )
        self.assertEqual(result["status"], "completed", result["answer"])
        self.assertEqual(len(requests), 3)
        self.assertTrue(
            any(
                event["payload"].get("llm_event_type") == "llm_parse_error"
                for event in result["run_events"]
            )
        )

    def test_genuine_json_and_documented_action_examples_are_preserved(self):
        for content in (
            '{"summary":"Task service","files":3}',
            '{"type":"final","content":"a genuine JSON answer"}',
            '{"type":[],"values":[1,2]}',
            "Example: " + json.dumps(UNFINISHED),
            "```json\n" + json.dumps(UNFINISHED) + "\n```",
            '{"tool_call":{"count":3}}',
        ):
            response = SimpleNamespace(content=content)
            validate_final_answer(response)
            self.assertEqual(response.content, content)
