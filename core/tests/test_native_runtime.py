"""Application adapters preserve ProtoLink's lifecycle, evidence and approval contracts."""

import asyncio
import json
import sys

from protolink import (
    AgentGroup,
    ApprovalDecision,
    ApprovalScope,
    RunContext,
    RunHandle,
    RunReport,
    Task,
    create_llm,
)
from runtime_support import NativeRuntimeCase

from protoagent_core.run_contracts import infer_run_contract
from protoagent_core.runtime_storage import output_redaction
from protoagent_core.verification import validate_completion


class NativeRuntimeTests(NativeRuntimeCase):
    async def test_bridge_exact_fingerprint_scope_and_multiple_pending(self):
        one = self.start_tool(
            self.coder, "create_file", {"path": str(self.root / "one"), "content": "one"}
        )
        two = self.start_tool(
            self.coder, "create_file", {"path": str(self.root / "two"), "content": "two"}
        )
        records = await self.pending(2)
        decision = ApprovalDecision(True, records[0].request.request_id)
        wrong = self.broker.resolve(
            decision, scope=ApprovalScope({"someone-else"}), fingerprint=records[0].fingerprint
        )
        self.assertEqual(wrong.status, "unknown")
        controls = asyncio.create_task(self.bridge.serve(one))
        try:
            async with asyncio.timeout(3):
                while not self.bridge.request_path.exists():
                    await asyncio.sleep(0.005)
            request = json.loads(self.bridge.request_path.read_text())
            self.assertNotIn('data_base64": "b', json.dumps(request))
            self.bridge.decision_path.write_text(
                json.dumps(
                    {"request_id": request["request_id"], "approved": True, "fingerprint": "wrong"}
                )
            )
            await asyncio.sleep(0.12)
            self.assertFalse((self.root / "one").exists())
            self.assertEqual(len(self.broker.pending(self.authorization.scope)), 2)
            for record in records:
                self.bridge.decision_path.write_text(
                    json.dumps(
                        {
                            "request_id": record.request.request_id,
                            "approved": True,
                            "fingerprint": record.fingerprint,
                        }
                    )
                )
                async with asyncio.timeout(3):
                    while any(
                        row.request.request_id == record.request.request_id
                        for row in self.broker.pending(self.authorization.scope)
                    ):
                        await asyncio.sleep(0.005)
            self.assertEqual((await one.result()).status, "completed")
            self.assertEqual((await two.result()).status, "completed")
        finally:
            controls.cancel()
            await asyncio.gather(controls, return_exceptions=True)

    async def test_cancel_pending_approval_unblocks_without_effect(self):
        handle = self.start_tool(
            self.coder, "create_file", {"path": str(self.root / "one"), "content": "one"}
        )
        await self.pending()
        controls = asyncio.create_task(self.bridge.serve(handle))
        self.bridge.cancel_path.write_text(json.dumps({"reason": "user Escape"}))
        await asyncio.wait_for(controls, 3)
        self.assertEqual((await handle.result()).status, "canceled")
        self.assertFalse((self.root / "one").exists())
        self.assertEqual(self.broker.records(self.authorization.scope)[0].status, "canceled")

    async def test_scope_cannot_admit_a_foreign_context(self):
        foreign = RunContext(workspace_uri=self.root.as_uri(), trace_id="foreign")
        result = await self.start_tool(
            self.coder,
            "create_file",
            {"path": str(self.root / "one"), "content": "one"},
            context=foreign,
        ).result()
        self.assertEqual(result.status, "failed")
        self.assertEqual(self.broker.records(self.authorization.scope), ())

    async def test_approval_and_preview_cannot_satisfy_completion(self):
        task = Task.create_infer(prompt="update the docs")
        self.context.attach_to_task(task)
        handle = self.start_tool(
            self.coder, "create_file", {"path": str(self.root / "one"), "content": "one"}
        )
        (record,) = await self.pending()
        preview_report = RunReport.from_events(handle.report.events, context=self.context)
        self.broker.resolve(
            ApprovalDecision(False, record.request.request_id),
            scope=self.authorization.scope,
            fingerprint=record.fingerprint,
        )
        await handle.result()
        accepted = await validate_completion(
            infer_run_contract("update the docs"), task, preview_report, self.attempt, self.broker
        )
        self.assertFalse(accepted.completion["satisfied"])
        self.assertEqual(accepted.completion["checks"][0]["code"], "execution_evidence_missing")
        self.assertFalse(accepted.repairable)

    async def test_passed_check_becomes_stale_after_external_edit(self):
        target = self.root / "one"
        written = await self.tool(
            self.coder, "create_file", {"path": str(target), "content": "one"}
        )
        checked = await self.tool(
            self.verifier,
            "execute_command",
            {
                "argv": [sys.executable, "-c", "print('passed')"],
                "cwd": str(self.root),
                "env": {},
                "timeout_seconds": 3,
                "max_output_bytes": 1024,
            },
        )
        task = checked.task
        report = RunReport.from_events((*written.report.events, *checked.report.events))
        contract = infer_run_contract("update a file")
        before = await validate_completion(contract, task, report, self.attempt, self.broker)
        self.assertTrue(before.completion["satisfied"])
        self.assertEqual(before.verification["status"], "passed")
        target.write_text("external edit")
        after = await validate_completion(contract, task, report, self.attempt, self.broker)
        self.assertEqual(after.verification["status"], "stale")
        self.assertFalse(after.completion["satisfied"])
        self.assertFalse(after.repairable)

    async def test_redaction_protects_native_snapshots_and_free_text_output(self):
        policy = output_redaction("a-known-private-value")
        self.store.redaction_policy = policy
        task = Task.create_infer(prompt="a-known-private-value")
        self.context.attach_to_task(task)
        task.metadata["recovery"] = {"data_base64": "original-bytes"}
        task.metadata["result"] = {
            "stdout": "a-known-private-value\x1b[2J",
            "api_key": "another-value",
        }
        self.store.save_task(task, metadata={"error": "a-known-private-value"})
        self.store.save_report(
            RunReport.from_task(task), metadata={"error": "a-known-private-value"}
        )
        saved = self.store.get_task(task.id).to_dict()
        self.assertNotIn("a-known-private-value", json.dumps(saved))
        self.assertNotIn("original-bytes", json.dumps(saved))
        self.assertNotIn("another-value", json.dumps(saved))
        self.assertEqual(task.metadata["recovery"]["data_base64"], "original-bytes")
        from pathlib import Path

        self.assertNotIn(b"a-known-private-value", Path(self.store.db_path).read_bytes())

    async def test_runhandle_normalizes_missing_final_as_uncertain_without_retry(self):
        calls = []

        class Client:
            async def send_task_streaming(self, url, task):
                calls.append(task.id)
                yield {"type": "task_progress", "task_id": task.id}
                raise ConnectionError("response lost after possible effect")

        result = await RunHandle.start(
            "runtime://lost", Task.create_infer(prompt="do work"), client=Client()
        ).result()
        self.assertEqual(result.status, "uncertain")
        self.assertIsNone(result.task)
        self.assertEqual(len(calls), 1)

    async def test_group_cleanup_cancels_live_native_process(self):
        async with AgentGroup([self.verifier]) as group:
            task = Task.create_tool_call(
                tool_name="execute_command",
                args={
                    "argv": [
                        sys.executable,
                        "-c",
                        "import time; print('ready',flush=True); time.sleep(60)",
                    ],
                    "cwd": str(self.root),
                    "env": {},
                    "timeout_seconds": 60,
                    "max_output_bytes": 1024,
                },
            )
            self.context.child(agent_name="verifier").attach_to_task(task)
            handle = group.run(self.verifier, task)
            (record,) = await self.pending()
            self.broker.resolve(
                ApprovalDecision(True, record.request.request_id),
                scope=self.authorization.scope,
                fingerprint=record.fingerprint,
            )
            async for event in handle.events():
                if event.type == "process.output":
                    break
        self.assertEqual((await handle.result()).status, "canceled")

    async def test_graph_enforces_two_repairs_with_real_native_receipts(self):
        await self.exercise_embedded_mesh("runtime", repairs=True)

    async def test_embedded_mesh_uses_broker_and_persists_delegated_receipts(self):
        await self.exercise_embedded_mesh("runtime")

    async def test_sse_mesh_preserves_delegated_native_receipts(self):
        await self.exercise_embedded_mesh("sse")

    async def exercise_embedded_mesh(self, transport, repairs=False):
        from unittest.mock import patch

        from protoagent_core.runtime import _run_agent_deck

        target = self.root / "actual.txt"
        release_file = self.root / "release-process"
        saw_live_process = False
        responses = [
            {
                "type": "agent_call",
                "agent": "coder",
                "action": "tool_call",
                "tool": "create_file",
                "args": {"path": str(target), "content": "native edit"},
            },
            {
                "type": "agent_call",
                "agent": "verifier",
                "action": "tool_call",
                "tool": "execute_command",
                "args": {
                    "argv": [
                        sys.executable,
                        "-c",
                        "import pathlib,time\nprint('verified', flush=True)\nwhile not pathlib.Path('release-process').exists(): time.sleep(0.01)",
                    ],
                    "cwd": str(self.root),
                    "env": {},
                    "timeout_seconds": 3,
                    "max_output_bytes": 1024,
                },
            },
            {"type": "final", "content": "Applied the file and checked it."},
        ]
        if repairs:
            import copy

            responses[1]["args"]["argv"] = [sys.executable, "-c", "raise SystemExit(7)"]
            repair = copy.deepcopy(responses)
            repair[0]["tool"] = "replace_file"
            responses += copy.deepcopy(repair) + copy.deepcopy(repair)
        llm = create_llm("mock", sequential_responses=responses)

        async def resolve_ui():
            nonlocal saw_live_process
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
                if not repairs and self.bridge.progress_path.exists():
                    records = [
                        json.loads(line)
                        for line in self.bridge.progress_path.read_text().splitlines()
                    ]
                    if any(
                        item.get("live_output", {}).get("text") == "verified\n" for item in records
                    ):
                        saw_live_process = True
                        release_file.write_text("release")
                await asyncio.sleep(0.01)

        controls = asyncio.create_task(resolve_ui())
        try:
            with (
                patch.dict("os.environ", {"PROTOAGENT_AGENT_TRANSPORT": transport}),
                patch("protoagent_core.agents.architect.create_selected_llm", return_value=llm),
                patch("protoagent_core.agents.coder.create_selected_llm", return_value=None),
                patch("protoagent_core.agents.explorer.create_selected_llm", return_value=None),
                patch(
                    "protolink.storage.SQLiteRunStore.list_task_records",
                    side_effect=AssertionError("Parent evidence must not scan stored worker tasks"),
                ),
            ):
                result = await asyncio.wait_for(
                    _run_agent_deck(
                        "Create a file and check it",
                        "ollama",
                        "mock",
                        str(self.root),
                        None,
                        self.bridge,
                        {"resolved": "small", "label": "Small", "configured": "small"},
                    ),
                    15,
                )
        finally:
            controls.cancel()
            await asyncio.gather(controls, return_exceptions=True)
        if repairs:
            self.assertEqual(result["status"], "failed", result["answer"])
            self.assertEqual(result["verification"]["attempts"], 3)
            self.assertEqual(len(result["verification"]["results"]), 3)
            self.assertEqual(len(result["approval_decisions"]), 6)
            return
        self.assertEqual(result["status"], "completed", result["answer"])
        self.assertTrue(
            saw_live_process, "Worker stdout must arrive while the process is still running"
        )
        self.assertEqual(target.read_text(), "native edit")
        self.assertEqual(
            result["verification"]["status"], "passed", result["completion_validation"]
        )
        self.assertEqual(len(result["approval_decisions"]), 2)
        self.assertTrue(any(event["type"] == "resource.changed" for event in result["run_events"]))
        self.assertTrue(result["run_report"]["validations"])
        self.assertEqual(result["run_report"]["final_task"]["state"], "completed")
        events = result["run_events"]
        self.assertEqual(len(events), len({event["event_id"] for event in events}))
        receipts = [
            event
            for event in events
            if event["type"] == "action.completed"
            and isinstance(event["payload"].get("action"), dict)
            and event["payload"].get("action", {}).get("name") in {"create_file", "execute_command"}
        ]
        self.assertEqual(len(receipts), 2)
        self.assertTrue(
            all(event["delegation_id"] and event["parent_action_id"] for event in receipts)
        )

    async def test_uncertain_checkpoint_stops_further_mutation_without_replay(self):
        from unittest.mock import patch

        save = self.checkpoints.save

        def fail_after_effect(change):
            if change.state == "applied":
                raise OSError("lost checkpoint completion write")
            save(change)

        target = self.root / "uncertain.txt"
        with patch.object(self.checkpoints, "save", side_effect=fail_after_effect):
            result = await self.tool(
                self.coder, "create_file", {"path": str(target), "content": "effect happened"}
            )
        self.assertEqual(result.status, "failed")
        self.assertEqual(target.read_text(), "effect happened")
        self.assertEqual(self.checkpoints.list_changes(limit=1)[0].state, "uncertain")
        accepted = await validate_completion(
            infer_run_contract("update a file"),
            result.task,
            result.report,
            self.attempt,
            self.broker,
        )
        self.assertEqual(accepted.completion["outcome"], "uncertain")
        self.assertFalse(accepted.repairable)
        retry = await self.start_tool(
            self.coder, "replace_file", {"path": str(target), "content": "do not replay"}
        ).result()
        self.assertEqual(retry.status, "failed")
        self.assertEqual(len(self.broker.records(self.authorization.scope)), 1)
        self.assertEqual(target.read_text(), "effect happened")

    async def test_cli_recovery_uses_native_handle_and_no_model(self):
        from unittest.mock import patch

        from protoagent_core.runtime import run_recovery

        target = self.root / "restore-me.txt"
        target.write_text("original")
        changed = await self.tool(
            self.coder, "replace_file", {"path": str(target), "content": "agent edit"}
        )
        with patch(
            "protoagent_core.agents.coder.create_selected_llm",
            side_effect=AssertionError("recovery constructed a model"),
        ):
            running = asyncio.create_task(
                asyncio.to_thread(
                    run_recovery,
                    str(self.root),
                    changed.output["change_id"],
                    None,
                    str(self.bridge.progress_path),
                )
            )
            async with asyncio.timeout(3):
                while not self.bridge.request_path.exists():
                    await asyncio.sleep(0.01)
            request = json.loads(self.bridge.request_path.read_text())
            self.assertEqual(request["action"]["name"], "restore_change")
            self.bridge.decision_path.write_text(
                json.dumps(
                    {
                        "approved": True,
                        "request_id": request["request_id"],
                        "fingerprint": request["fingerprint"],
                    }
                )
            )
            result = await asyncio.wait_for(running, 3)
        self.assertEqual(result["status"], "answered", result["answer"])
        self.assertEqual(target.read_text(), "original")
        self.assertTrue(any(event["type"] == "resource.changed" for event in result["run_events"]))

    async def test_preflight_cancellation_never_constructs_model_deck(self):
        from unittest.mock import patch

        from protoagent_core.runtime import _run_agent_deck

        self.bridge.cancel_path.write_text(json.dumps({"reason": "early Escape"}))
        with patch(
            "protoagent_core.runtime.create_agent_deck",
            side_effect=AssertionError("model deck constructed"),
        ):
            result = await _run_agent_deck(
                "update a file",
                "ollama",
                "mock",
                str(self.root),
                None,
                self.bridge,
                {"resolved": "small", "configured": "small", "label": "Small"},
            )
        self.assertEqual(result["status"], "canceled")
        self.assertTrue(result["run_context"]["canceled"])
        self.assertEqual(result["run_report"]["final_task"]["state"], "canceled")
