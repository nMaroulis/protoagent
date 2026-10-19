"""Malformed source selections use native correction; actual writes still need approval."""

import hashlib
import json
from unittest.mock import patch

import httpx
from protolink import ApprovalDecision, RunBudget, RunHandle, Task
from protolink.llms.factory import create_llm
from protolink.llms.server.ollama_client import OllamaLLM
from runtime_support import NativeRuntimeCase

from protoagent_core.agents.coder import create_coder_agent
from protoagent_core.request_budget import configure_agent_context


class SourceFeedbackTests(NativeRuntimeCase):
    async def test_double_escaped_newlines_are_corrected_before_approval_or_mutation(self):
        self.context.budget = RunBudget(max_tool_calls=1)
        sample = self.root / "sample.py"
        sample.write_text("value = 0\n")
        invalid = {**self.arguments(), "old": "value = 0\n", "new": "value = 42\\n"}
        model = create_llm(
            "mock",
            sequential_responses=[
                {"type": "tool_call", "tool": "edit_file", "args": invalid},
                {"type": "tool_call", "tool": "edit_file", "args": self.arguments()},
                {"type": "final", "content": "Updated sample.py."},
            ],
        )
        handle = self.start_inference(self.make_coder(model))
        (record,) = await self.pending()
        self.assertEqual(sample.read_text(), "value = 0\n")
        self.assertEqual(self.checkpoints.list_changes(), [])
        self.assertEqual(len(self.broker.records(self.authorization.scope)), 1)
        self.broker.resolve(
            ApprovalDecision(True, record.request.request_id),
            scope=self.authorization.scope,
            fingerprint=record.fingerprint,
        )
        result = await handle.result()
        self.assertEqual(result.status, "completed", result.error)
        self.assertEqual(sample.read_text(), "value = 42\n")
        self.assertIn("introduced_python_syntax", json.dumps(result.report.to_dict()))
        self.assertEqual(len(self.checkpoints.list_changes(state="applied")), 1)

    async def test_create_and_replace_reject_introduced_syntax_without_effects(self):
        sample = self.root / "sample.py"
        sample.write_text("value = 0\n")
        for name, path in (("replace_file", sample), ("create_file", self.root / "new.py")):
            with self.subTest(tool=name):
                result = await self.start_tool(
                    self.coder, name, {"path": str(path), "content": "value = 42\\n"}
                ).result()
                self.assertEqual(result.status, "failed")
                self.assertIn("introduced_python_syntax", json.dumps(result.report.to_dict()))
                self.assertFalse(self.broker.records(self.authorization.scope))
                self.assertFalse(self.checkpoints.list_changes())
        self.assertFalse((self.root / "new.py").exists())
        self.assertEqual(sample.read_text(), "value = 0\n")

    async def test_python_strings_keep_literal_escapes_and_validation_does_not_import(self):
        content = "import a_dependency_that_is_not_installed\ntext = '\\n'\n"
        sample = self.root / "sample.py"
        result = await self.tool(
            self.coder, "create_file", {"path": str(sample), "content": content}
        )
        self.assertEqual(result.status, "completed", result.error)
        self.assertEqual(sample.read_text(), content)

    async def test_preexisting_syntax_errors_can_be_repaired_incrementally(self):
        sample = self.root / "sample.py"
        sample.write_text("x =\ny =\n")
        result = await self.tool(
            self.coder,
            "edit_file",
            {
                "path": str(sample),
                "old": "x =",
                "new": "x = 1",
                "expected_revision": hashlib.sha256(sample.read_bytes()).hexdigest(),
            },
        )
        self.assertEqual(result.status, "completed", result.error)
        self.assertEqual(sample.read_text(), "x = 1\ny =\n")

    async def test_valid_create_and_replace_still_enforce_native_write_denial(self):
        self.context.permissions = {"filesystem.write": "deny"}
        sample = self.root / "sample.py"
        sample.write_text("value = 0\n")
        for name, path in (("replace_file", sample), ("create_file", self.root / "new.py")):
            with self.subTest(tool=name):
                result = await self.start_tool(
                    self.coder, name, {"path": str(path), "content": "value = 42\n"}
                ).result()
                self.assertEqual(result.status, "failed")
                self.assertTrue(
                    any(event.type == "action.denied" for event in result.report.events)
                )
        self.assertEqual(sample.read_text(), "value = 0\n")
        self.assertFalse((self.root / "new.py").exists())
        self.assertFalse(self.broker.records(self.authorization.scope))

    def arguments(self, old="value = 0", revision=None):
        return {
            "path": str(self.root / "sample.py"),
            "old": old,
            "new": "value = 42",
            "expected_revision": revision or hashlib.sha256(b"value = 0\n").hexdigest(),
        }

    def make_coder(self, model):
        with patch("protoagent_core.agents.coder.create_selected_llm", return_value=model):
            agent = create_coder_agent(
                workspace=str(self.root),
                transport=None,
                prompt_profile="small",
                approval_handler=self.broker,
                authorization=self.authorization,
                attempt=self.attempt,
                checkpoints=self.checkpoints,
            )
        configure_agent_context(agent, self.attempt.record, compact_protocol=True)
        return agent

    def start_inference(self, agent):
        task = Task.create_infer("Change sample.py value to 42")
        self.context.child(agent_name="coder").attach_to_task(task)
        handle = RunHandle.start(agent, task)
        self.handles.append(handle)
        return handle

    async def test_invented_old_text_can_be_reread_and_corrected_in_native_and_json_modes(self):
        for native in (False, True):
            with self.subTest(native=native):
                sample = self.root / "sample.py"
                sample.write_text("value = 0\n")
                self.attempt.record.frozen = False
                actions = [
                    {
                        "type": "tool_call",
                        "tool": "edit_file",
                        "args": self.arguments("invented source"),
                    },
                    {"type": "tool_call", "tool": "read_file", "args": {"path": str(sample)}},
                    {"type": "tool_call", "tool": "edit_file", "args": self.arguments()},
                    {"type": "final", "content": "Applied the focused edit."},
                ]
                requests = []
                responses = iter(actions)
                initial_approvals = len(self.broker.records(self.authorization.scope))

                def respond(request):
                    requests.append(json.loads(request.content))
                    if len(requests) == 2:
                        self.assertEqual(sample.read_text(), "value = 0\n")
                        self.assertEqual(
                            len(self.broker.records(self.authorization.scope)), initial_approvals
                        )
                        self.assertFalse(self.attempt.record.frozen)
                    action = next(responses)
                    message = {"content": json.dumps(action)}
                    if native:
                        message = (
                            {"content": action["content"]}
                            if action["type"] == "final"
                            else {
                                "tool_calls": [
                                    {
                                        "function": {
                                            "name": action["tool"],
                                            "arguments": action["args"],
                                        }
                                    }
                                ]
                            }
                        )
                    return httpx.Response(
                        200, content=json.dumps({"message": message, "done": True}) + "\n"
                    )

                client = httpx.AsyncClient
                with (
                    patch.object(OllamaLLM, "validate_connection", return_value=True),
                    patch(
                        "httpx.AsyncClient",
                        side_effect=lambda **kw: client(
                            transport=httpx.MockTransport(respond), **kw
                        ),
                    ),
                ):
                    agent = self.make_coder(
                        OllamaLLM(
                            base_url="http://provider.invalid",
                            model="small",
                            supports_tool_calling=native,
                        )
                    )
                    handle = self.start_inference(agent)
                    (approval,) = await self.pending()
                    self.assertEqual(approval.request.action.name, "replace_file")
                    self.assertIn("filesystem.write", approval.request.action.capabilities)
                    self.assertIn(
                        "value = 42",
                        json.dumps([a.to_dict() for a in approval.request.action.artifacts]),
                    )
                    self.broker.resolve(
                        ApprovalDecision(True, approval.request.request_id),
                        scope=self.authorization.scope,
                        fingerprint=approval.fingerprint,
                    )
                    result = await handle.result()
                self.assertEqual(result.status, "completed", result.error)
                self.assertEqual(sample.read_text(), "value = 42\n")
                self.assertIn("ambiguous_source_match", json.dumps(requests[1]["messages"]))
                self.assertFalse(any(e.type == "action.failed" for e in result.report.events))
                self.assertTrue(any(e.type == "action.rejected" for e in result.report.events))
                self.assertEqual(
                    len(self.broker.records(self.authorization.scope)), initial_approvals + 1
                )

    async def test_successful_reread_does_not_reset_native_correction_limit(self):
        (self.root / "sample.py").write_text("value = 0\n")
        bad = {"type": "tool_call", "tool": "edit_file", "args": self.arguments("invented")}
        model = create_llm(
            "mock",
            max_tool_corrections=1,
            sequential_responses=[
                bad,
                {"type": "tool_call", "tool": "read_file", "args": {"path": "sample.py"}},
                bad,
            ],
        )
        result = await self.start_inference(self.make_coder(model)).result()
        self.assertEqual(result.status, "failed")
        self.assertTrue(any(e.type == "tool.correction.exhausted" for e in result.report.events))
        self.assertFalse(self.broker.records(self.authorization.scope))
        self.assertEqual((self.root / "sample.py").read_text(), "value = 0\n")

    async def test_stale_revision_rejection_is_native_no_change_feedback(self):
        (self.root / "sample.py").write_text("value = 0\n")
        result = await self.start_tool(
            self.coder, "edit_file", self.arguments(revision="outdated")
        ).result()
        self.assertEqual(result.status, "failed")
        self.assertIn("stale_source_revision", json.dumps(result.report.to_dict()))
        self.assertFalse(any(e.type == "action.requested" for e in result.report.events))
        self.assertFalse(any(e.type == "process.started" for e in result.report.events))
        self.assertFalse(self.broker.records(self.authorization.scope))
        self.assertFalse(self.attempt.record.frozen)

    async def test_identical_edit_is_rejected_without_approval_checkpoint_or_write(self):
        sample = self.root / "sample.py"
        sample.write_text("value = 0\n")
        before = sample.stat().st_mtime_ns
        result = await self.start_tool(
            self.coder, "edit_file", {**self.arguments(), "new": "value = 0"}
        ).result()
        self.assertEqual(result.status, "failed")
        self.assertIn("unchanged_source", json.dumps(result.report.to_dict()))
        self.assertEqual(sample.stat().st_mtime_ns, before)
        self.assertFalse(self.broker.records(self.authorization.scope))
        self.assertFalse(list(self.attempt.file_changes()))
        self.assertFalse(self.attempt.record.frozen)

    async def test_returned_source_revision_allows_chained_edits_and_rejects_external_changes(self):
        sample = self.root / "sample.py"
        sample.write_text("value = 0\n")
        first = await self.tool(self.coder, "edit_file", self.arguments())
        self.assertEqual(first.status, "completed", first.error)
        revision = first.output["source_revision"]
        second = await self.tool(
            self.coder,
            "edit_file",
            {
                "path": "sample.py",
                "old": "value = 42",
                "new": "value = 43",
                "expected_revision": revision,
            },
        )
        self.assertEqual(second.status, "completed", second.error)
        self.assertEqual(sample.read_text(), "value = 43\n")
        self.assertNotEqual(second.output["source_revision"], revision)
        receipts = [
            event.payload["result"]
            for event in second.report.events
            if event.type == "action.completed" and "result" in event.payload
        ]
        self.assertTrue(receipts)
        self.assertTrue(
            all(receipt["resource"] == second.output["resource"] for receipt in receipts)
        )
        self.assertTrue(
            all(receipt["change_id"] == second.output["change_id"] for receipt in receipts)
        )
        sample.write_text("value = 100\n")
        rejected = await self.start_tool(
            self.coder,
            "edit_file",
            {
                "path": "sample.py",
                "old": "value = 43",
                "new": "value = 44",
                "expected_revision": second.output["source_revision"],
            },
        ).result()
        self.assertEqual(rejected.status, "failed")
        self.assertIn("stale_source_revision", json.dumps(rejected.report.to_dict()))
        self.assertEqual(sample.read_text(), "value = 100\n")
        self.assertEqual(len(self.broker.records(self.authorization.scope)), 2)

    async def test_valid_edit_cannot_bypass_native_write_denial(self):
        (self.root / "sample.py").write_text("value = 0\n")
        self.context.permissions = {"filesystem.write": "deny"}
        result = await self.start_tool(self.coder, "edit_file", self.arguments()).result()
        self.assertEqual(result.status, "failed")
        self.assertTrue(any(e.type == "action.denied" for e in result.report.events))
        self.assertFalse(self.broker.records(self.authorization.scope))
        self.assertEqual((self.root / "sample.py").read_text(), "value = 0\n")

    async def test_broken_quote_must_be_copied_literally_before_repairing_it(self):
        sample = self.root / "sample.py"
        broken = 'message = "broken.\\"\n'
        sample.write_text(broken)
        arguments = {
            "path": str(sample),
            "old": 'message = "broken."\n',
            "new": 'message = "fixed."\n',
            "expected_revision": hashlib.sha256(broken.encode()).hexdigest(),
        }
        rejected = await self.start_tool(self.coder, "edit_file", arguments).result()
        self.assertEqual(rejected.status, "failed")
        self.assertIn("ambiguous_source_match", json.dumps(rejected.report.to_dict()))
        self.assertEqual(sample.read_text(), broken)
        self.assertFalse(self.broker.records(self.authorization.scope))
        applied = await self.tool(self.coder, "edit_file", {**arguments, "old": broken})
        self.assertEqual(applied.status, "completed", applied.error)
        self.assertEqual(sample.read_text(), 'message = "fixed."\n')
