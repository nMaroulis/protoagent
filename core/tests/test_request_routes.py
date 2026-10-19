"""Request-sized tool surfaces with native permissions and real runtime traces."""

import json
from unittest.mock import AsyncMock, patch

from protolink.llms.factory import create_llm
from runtime_support import NativeRuntimeCase

from protoagent_core.agents.common import RUNTIME_SCOPES
from protoagent_core.agents.deck import create_agent_deck
from protoagent_core.run_contracts import infer_run_contract
from protoagent_core.runtime import _run_agent_deck


class RequestRouteTests(NativeRuntimeCase):
    async def run_prompt(self, prompt, responses, *, profile="small", seen=None):
        replies = iter(responses)

        def respond(history, current):
            if seen is not None:
                seen.append(history.to_list())
            return next(replies)

        with patch(
            "protoagent_core.agents.common.create_llm_from_config",
            side_effect=lambda *args: create_llm("mock", response_callback=respond),
        ):
            return await _run_agent_deck(
                prompt,
                "mock",
                "offline",
                str(self.root),
                None,
                self.bridge,
                {"resolved": profile},
                tester_enabled=True,
                scout_enabled=True,
                mcp_enabled=True,
            )

    async def test_greeting_uses_one_model_call_without_project_discovery_or_tools(self):
        # An unrelated broken project config must not break a conversation.
        (self.root / ".protoagent").mkdir()
        (self.root / ".protoagent/project.json").write_text("invalid JSON")
        for profile in ("small", "medium"):
            with (
                self.subTest(profile=profile),
                patch(
                    "protoagent_core.task_record.discover_checks",
                    side_effect=AssertionError("Conversation discovered checks"),
                ),
            ):
                seen = []
                result = await self.run_prompt(
                    "hi", [{"type": "final", "content": "Hi!"}], profile=profile, seen=seen
                )
                self.assertEqual(result["status"], "completed", result["answer"])
                self.assertEqual(result["answer"], "Hi!")
                self.assertEqual(len(seen), 1)
                text = json.dumps(seen)
                self.assertNotIn("acceptance_criteria", text)
                self.assertNotIn("Runtime task packet", text)
                self.assertNotIn("python-tests", text)
                self.assertEqual(set(result["context_admission"]), {"architect"})
                self.assertEqual(result["context_admission"]["architect"]["schema_tokens"], 0)
                self.assertFalse(result["approval_requests"])
                self.assertFalse(result["task_record"]["required_checks"])
                self.assertFalse(result["task_record"]["acceptance_criteria"])
                self.assertFalse(
                    any(e["type"].startswith("user_input.") for e in result["run_events"])
                )
                self.assertFalse(any(e["type"] == "action.requested" for e in result["run_events"]))

    def test_decks_expose_only_workers_needed_by_the_original_request(self):
        for prompt, roles in (
            ("hello", {"architect"}),
            ("Explain this project", {"architect", "explorer"}),
            ("Run the tests", {"architect", "verifier"}),
            ("Fix sample.py", {"architect", "explorer", "coder", "verifier"}),
        ):
            with (
                self.subTest(prompt=prompt),
                patch("protoagent_core.agents.common.create_llm_from_config", return_value=None),
            ):
                deck = create_agent_deck(
                    workspace=str(self.root),
                    transport=None,
                    local_children=True,
                    prompt_profile="small",
                    run_contract=infer_run_contract(prompt),
                )
                self.assertEqual(set(deck), roles)
                parent = deck["architect"]
                if prompt == "hello":
                    self.assertFalse(parent.tools)
                    self.assertFalse(parent.subagents)
                if prompt == "Explain this project":
                    self.assertFalse(
                        {"coder", "tester", "run_check", "execute_command", "plan_task"}
                        & parent.tools.keys()
                    )

    async def test_repository_question_reads_source_without_a_coding_plan(self):
        (self.root / "README.md").write_text("A task tracker.")
        seen = []
        result = await self.run_prompt(
            "What's this project about?",
            [
                {"type": "tool_call", "tool": "read_file", "args": {"path": "README.md"}},
                {"type": "final", "content": "A task tracker."},
            ],
            seen=seen,
        )
        self.assertEqual(result["status"], "completed", result["answer"])
        self.assertIn("A task tracker", result["answer"])
        self.assertNotIn("acceptance_criteria", json.dumps(seen))
        self.assertTrue(any(e["type"] == "action.completed" for e in result["run_events"]))
        self.assertFalse(result["approval_requests"])
        self.assertNotIn("coder", result["context_admission"])

    async def test_declined_question_cannot_be_followed_by_an_invented_coder_task(self):
        with patch.object(self.bridge, "ask_user", new=AsyncMock(return_value=None)):
            result = await self.run_prompt(
                "Explain this project",
                [
                    {
                        "type": "tool_call",
                        "tool": "ask_user",
                        "args": {"question": "What should I change?"},
                    },
                    {
                        "type": "tool_call",
                        "tool": "coder",
                        "args": {"task": "Replace server.py's message with hi"},
                    },
                    {"type": "final", "content": "No change was requested."},
                ],
            )
        self.assertTrue(any(e["type"] == "user_input.declined" for e in result["run_events"]))
        self.assertFalse(result["approval_requests"])
        self.assertFalse(result["completion_validation"]["applied"])
        self.assertFalse(
            any(
                e["type"] == "action.requested"
                and e["payload"].get("action", {}).get("name") == "coder"
                for e in result["run_events"]
            )
        )
        self.assertFalse(list(self.checkpoints.list_changes(limit=10)))

    async def test_native_permission_ceiling_blocks_a_write_even_with_a_registered_coder(self):
        self.context.permissions = infer_run_contract("Explain this project").permissions(
            RUNTIME_SCOPES
        )
        result = await self.start_tool(
            self.coder,
            "create_file",
            {
                "path": str(self.root / "unexpected.py"),
                "content": "unexpected = True\n",
            },
        ).result()
        self.assertEqual(result.status, "failed")
        self.assertTrue(any(e.type == "action.denied" for e in result.report.events))
        self.assertFalse(self.broker.records(self.authorization.scope))
        self.assertFalse((self.root / "unexpected.py").exists())

    def test_cli_conversation_skips_loom_tags_and_target_map(self):
        from protoagent_core.agent_engine import process_prompt

        with (
            patch.dict("os.environ", {}),
            patch("protoagent_core.agent_engine._tagged_file_context", side_effect=AssertionError),
            patch(
                "protoagent_core.agent_engine._context_pack_for_prompt", side_effect=AssertionError
            ),
            patch("protoagent_core.agent_engine.build_context_map", side_effect=AssertionError),
            patch("protoagent_core.agent_engine.remember_valid_provider"),
            patch("protoagent_core.history.persist_architect_turn"),
            patch(
                "protoagent_core.runtime.run_selected_model",
                return_value={
                    "status": "completed",
                    "provider": "mock",
                    "model": "offline",
                    "answer": "Hi!",
                },
            ) as run,
        ):
            response = json.loads(process_prompt("hi", str(self.root)))
        self.assertEqual(response["answer"], "Hi!")
        self.assertEqual(response["status"], "answered")
        self.assertEqual(run.call_args.args[0], "hi")
