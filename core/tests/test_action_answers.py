"""A provider's intended action must execute through ProtoLink or fail visibly."""

import json
from unittest.mock import patch

import httpx
from protolink.llms.metrics import estimate_token_count
from protolink.llms.server.ollama_client import OllamaLLM
from runtime_support import NativeRuntimeCase

from protoagent_core.runtime import _run_agent_deck

UNFINISHED = {"tool_call": {"name": "explorer", "args": {"tool": "read_file", "path": "README.md"}}}


class ActionAnswerTests(NativeRuntimeCase):
    async def test_flat_worker_read_executes_in_native_and_json_modes(self):
        (self.root / "README.md").write_text("A small task management service.\n")
        for native in (False, True):
            with self.subTest(native=native):
                call = {"name": "read_file", "arguments": {"path": "README.md"}}
                result, requests = await self.run_replies(
                    [
                        {"tool_calls": [{"function": call}]}
                        if native
                        else {
                            "content": json.dumps(
                                {
                                    "type": "tool_call",
                                    "tool": "read_file",
                                    "args": {"path": "README.md"},
                                }
                            )
                        },
                        {"content": "A task service."}
                        if native
                        else {
                            "content": json.dumps({"type": "final", "content": "A task service."})
                        },
                    ],
                    native=native,
                )
                self.assertEqual(result["status"], "completed", result["answer"])
                self.assertEqual(len(requests), 2)
                self.assertIn(
                    "A small task management service", json.dumps(requests[1]["messages"])
                )
                if native:
                    tools = {tool["function"]["name"] for tool in requests[0]["tools"]}
                    self.assertIn("read_file", tools)
                    self.assertIn("run_check", tools)
                    self.assertNotIn("worker_packet", tools)
                    self.assertEqual(
                        result["context_admission"]["architect"]["schema_tokens"],
                        estimate_token_count(requests[0]["tools"]),
                    )
                self.assertTrue(
                    any(
                        e["type"] == "action.completed"
                        and e.get("agent_name") == "explorer"
                        and e["payload"].get("action", {}).get("name") == "read_file"
                        for e in result["run_events"]
                    )
                )

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

    async def test_screenshot_wrapper_gets_native_correction_then_a_real_read_in_either_mode(self):
        (self.root / "README.md").write_text("A small task management service.\n")
        action = {
            "type": "agent_call",
            "agent": "explorer",
            "action": "tool_call",
            "tool": "read_file",
            "args": {"path": "README.md"},
        }
        for native in (False, True):
            with self.subTest(native=native):
                result, requests = await self.run_replies(
                    [
                        {"content": json.dumps(UNFINISHED)},
                        {"content": json.dumps(action)},
                        {"content": json.dumps({"type": "final", "content": "A task service."})},
                    ],
                    native=native,
                )
                self.assertEqual(result["status"], "completed", result["answer"])
                self.assertEqual(result["answer"], "A task service.")
                self.assertEqual(len(requests), 3)
                self.assertIn("Action validation failed", json.dumps(requests[1]["messages"]))
                self.assertEqual(
                    sum(
                        event["type"] == "action.completed"
                        and event.get("agent_name") == "explorer"
                        and event["payload"].get("action", {}).get("name") == "read_file"
                        for event in result["run_events"]
                        if isinstance(event["payload"].get("action"), dict)
                    ),
                    1,
                )

    async def test_repeated_malformed_wrappers_stop_at_the_engine_parse_limit(self):
        for native in (False, True):
            with self.subTest(native=native):
                result, requests = await self.run_replies(
                    [{"content": json.dumps(UNFINISHED)}] * 3, native=native
                )
                self.assertEqual(result["status"], "failed", result["answer"])
                self.assertEqual(len(requests), 3)
                self.assertFalse(
                    any(event["type"] == "subagent.started" for event in result["run_events"])
                )
                self.assertFalse(
                    any(
                        event["payload"].get("llm_event_type") == "llm_final"
                        for event in result["run_events"]
                    )
                )

    async def test_native_plan_rejection_is_corrected_without_restarting(self):
        (self.root / "sample.py").write_text("value = 0\n")

        def plan(arguments):
            return {"tool_calls": [{"function": {"name": "plan_task", "arguments": arguments}}]}

        result, requests = await self.run_replies(
            [
                plan({"paths": ["sample.py"], "criteria": ["Value is 42"], "check_ids": []}),
                plan({"paths": ["sample.py", "test_sample.py"], "criteria": ["Value is 42"]}),
                {"content": "Plan includes the runner and a regression test."},
            ],
            native=True,
        )
        self.assertEqual(result["status"], "completed", result["answer"])
        self.assertEqual(len(requests), 3)
        self.assertIn("check_required", json.dumps(requests[1]["messages"]))
        self.assertIn("python-tests", json.dumps(requests[1]["messages"]))
        self.assertEqual((self.root / "sample.py").read_text(), "value = 0\n")
        self.assertTrue(any(event["type"] == "action.rejected" for event in result["run_events"]))
        self.assertFalse(any(event["type"] == "action.failed" for event in result["run_events"]))

    async def test_native_tools_only_inference_is_rejected_before_correct_delegation(self):
        (self.root / "README.md").write_text("A task management service.\n")
        result, requests = await self.run_replies(
            [
                {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "protolink_delegate_agent",
                                "arguments": {
                                    "agent": "verifier",
                                    "prompt": "Explain the project",
                                },
                            }
                        }
                    ]
                },
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
                {"content": "A task management service."},
            ],
            native=True,
        )
        self.assertEqual(result["status"], "completed", result["answer"])
        self.assertEqual(len(requests), 3)
        self.assertIn("unsupported_inference", json.dumps(requests[1]["messages"]))
        self.assertIn("A task management service", json.dumps(requests[2]["messages"]))
        self.assertFalse(any(event["type"] == "action.failed" for event in result["run_events"]))

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

    async def test_genuine_json_and_explicit_final_data_are_preserved_by_the_native_engine(self):
        for content in (
            '{"summary":"Task service","files":3}',
            '{"type":"final","content":"a genuine JSON answer"}',
            '{"type":[],"values":[1,2]}',
            "Example: " + json.dumps(UNFINISHED),
            "A protocol example:\n```json\n" + json.dumps(UNFINISHED) + "\n```",
        ):
            expected = "a genuine JSON answer" if '"type":"final"' in content else content
            result, requests = await self.run_replies([{"content": content}], native=True)
            self.assertEqual(result["status"], "completed", result["answer"])
            self.assertEqual(result["answer"], expected)
            self.assertEqual(len(requests), 1)
        explicit = json.dumps({"type": "final", "content": json.dumps(UNFINISHED)})
        result, requests = await self.run_replies([{"content": explicit}], native=True)
        self.assertEqual(result["status"], "completed", result["answer"])
        self.assertEqual(result["answer"], json.dumps(UNFINISHED))
        self.assertEqual(len(requests), 1)
