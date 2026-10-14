"""Manual verification preserves native evidence and stops execution immediately."""

import asyncio
import json
import sys
from unittest.mock import patch

from protolink.llms import create_llm
from runtime_support import NativeRuntimeCase

from protoagent_core.runtime import _run_agent_deck


class VerificationHandoffTests(NativeRuntimeCase):
    async def test_applied_changes_and_failed_check_stop_without_further_effects(self):
        (self.root / ".protoagent").mkdir()
        (self.root / ".protoagent/project.json").write_text(
            json.dumps(
                {
                    "checks": [
                        {
                            "id": "regression",
                            "argv": [
                                sys.executable,
                                "-c",
                                "raise ModuleNotFoundError(\"No module named 'fastapi'\")",
                            ],
                            "env": {},
                            "paths": ["server.py", "test_server.py"],
                        }
                    ]
                }
            )
        )
        responses = [
            {
                "type": "agent_call",
                "agent": "coder",
                "action": "tool_call",
                "tool": "create_file",
                "args": {"path": "server.py", "content": "value = 42\n"},
            },
            {
                "type": "agent_call",
                "agent": "coder",
                "action": "tool_call",
                "tool": "create_file",
                "args": {"path": "test_server.py", "content": "# Regression fixture\n"},
            },
            {
                "type": "agent_call",
                "agent": "verifier",
                "action": "tool_call",
                "tool": "run_check",
                "args": {"check_id": "regression", "phase": "verify"},
            },
            {
                "type": "tool_call",
                "tool": "ask_user",
                "args": {
                    "question": "Verification failed: fastapi is missing. Install it or retry tests?",
                    "options": ["Install", "Retry"],
                },
            },
        ]
        calls = []

        def script(history, prompt):
            calls.append(prompt)
            self.assertLessEqual(len(calls), len(responses), "Handoff must stop model requests")
            return responses[len(calls) - 1]

        llm = create_llm("mock", response_callback=script)

        async def answer_controls():
            seen = set()
            while True:
                if self.bridge.request_path.exists():
                    request = json.loads(self.bridge.request_path.read_text())
                    if request["request_id"] not in seen:
                        seen.add(request["request_id"])
                        self.bridge.decision_path.write_text(
                            json.dumps(
                                {
                                    "approved": True,
                                    "request_id": request["request_id"],
                                    "fingerprint": request["fingerprint"],
                                }
                            )
                        )
                if self.bridge.input_request_path.exists():
                    request = json.loads(self.bridge.input_request_path.read_text())
                    self.bridge.input_response_path.write_text(
                        json.dumps(
                            {
                                **{
                                    key: request[key]
                                    for key in ("request_id", "run_id", "task_id", "action_id")
                                },
                                "answer": "do not run it its ok Ill do it ",
                            }
                        )
                    )
                    return
                await asyncio.sleep(0.005)

        controls = asyncio.create_task(answer_controls())
        try:
            with (
                patch.dict("os.environ", {"PROTOAGENT_AGENT_TRANSPORT": "local"}),
                patch("protoagent_core.agents.architect.create_selected_llm", return_value=llm),
                patch("protoagent_core.agents.coder.create_selected_llm", return_value=None),
                patch("protoagent_core.agents.explorer.create_selected_llm", return_value=None),
                patch("protoagent_core.agents.tester.create_selected_llm", return_value=None),
            ):
                result = await asyncio.wait_for(
                    _run_agent_deck(
                        "Create server.py and add regression tests",
                        "mock",
                        "offline",
                        str(self.root),
                        None,
                        self.bridge,
                        {"resolved": "small", "label": "Small"},
                    ),
                    5,
                )
        finally:
            controls.cancel()
            await asyncio.gather(controls, return_exceptions=True)
        self.assertEqual(result["status"], "unverified", result["answer"])
        self.assertEqual(len(calls), 4)
        self.assertEqual((self.root / "server.py").read_text(), "value = 42\n")
        self.assertTrue((self.root / "test_server.py").exists())
        completion, verification = result["completion_validation"], result["verification"]
        self.assertTrue(completion["applied"])
        self.assertFalse(completion["satisfied"])
        self.assertFalse(completion["verified"])
        self.assertFalse(completion["criteria_supported"])
        self.assertEqual(verification["status"], "deferred")
        self.assertEqual(verification["last_attempt_status"], "failed")
        # The failed check hands back to Graph, which opens the next phase
        # before asking. The user's stop prevents any repair effect or rerun.
        self.assertEqual(verification["attempts"], 2)
        self.assertEqual(len(verification["results"]), 1)
        self.assertEqual(verification["results"][0]["attempt"], 1)
        self.assertEqual(verification["results"][0]["exit_code"], 1)
        self.assertIn("ModuleNotFoundError", verification["results"][0]["stderr"])
        self.assertIn("Changes applied: server.py, test_server.py.", result["answer"])
        self.assertIn("Verification: deferred to you.", result["answer"])
        self.assertIn("could not import fastapi", result["answer"])
        self.assertNotIn("Run blocked", result["answer"])
        self.assertEqual(len(result["approval_decisions"]), 3)
        native = result["run_report"]
        self.assertNotEqual(native["metadata"]["transport_task_status"], "completed")
        self.assertEqual(native["metadata"]["application_status"], "unverified")
        events = result["run_events"]
        choice_index = next(
            i for i, event in enumerate(events) if event["type"] == "verification.deferred"
        )
        self.assertFalse(
            any(event["type"] == "llm.started" for event in events[choice_index + 1 :])
        )
        process_receipts = [
            event
            for event in events
            if event["type"] == "action.completed"
            and isinstance(event["payload"].get("action"), dict)
            and event["payload"]["action"].get("name") == "execute_command"
        ]
        self.assertEqual(len(process_receipts), 1)
