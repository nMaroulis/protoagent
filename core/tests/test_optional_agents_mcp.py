"""Optional-worker composition and real MCP calls through native approvals."""

import asyncio
import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from protolink import ActionDeniedError, Task
from runtime_support import NativeRuntimeCase

from protoagent_core import agent_engine, config
from protoagent_core.agents.architect import architect_system_prompt
from protoagent_core.agents.deck import agent_manifest, create_agent_deck
from protoagent_core.agents.mcp import create_mcp_agent
from protoagent_core.mcp import MCP_TOOLS, MCPAccess, mcp_command, validate_server
from protoagent_core.runtime import run_selected_model

SERVER = """
from pathlib import Path
import sys
from mcp.server.mcpserver import MCPServer
root = Path(sys.argv[1])
with (root / "sessions").open("a") as f: f.write("start\\n")
server = MCPServer("protoagent-test", log_level="ERROR")
@server.tool(structured_output=True)
def lookup(key: str) -> dict[str, str]:
    with (root / "calls").open("a") as f: f.write(key + "\\n")
    return {"key": key, "value": "found"}
@server.tool()
def large() -> str:
    return "x" * 20000
@server.tool()
def failure() -> str:
    raise ValueError("fixture tool failure")
@server.tool()
def slow() -> str:
    import time
    (root / "slow-started").touch()
    time.sleep(10)
    return "late"
for index in range(14):
    server.add_tool(lookup, name=f"unused_{index}", structured_output=True)
if len(sys.argv) > 2:
    server.run(transport=sys.argv[2], host="127.0.0.1", port=int(sys.argv[3]))
else:
    server.run()
"""


class OptionalAgentTests(unittest.TestCase):
    def test_defaults_persistence_manifest_and_required_roles(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(config, "CONFIG_DIR", Path(directory)),
            patch.object(config, "CONFIG_PATH", Path(directory) / "config.json"),
        ):
            self.assertTrue(config.optional_agent_enabled("tester", {"optional_agents": {}}))
            self.assertFalse(config.optional_agent_enabled("mcp", config.default_config()))
            result = json.loads(agent_engine.configure_optional_agent("tester", False))
            agents = {entry["name"]: entry for entry in result["agents"]}
            self.assertFalse(agents["tester"]["enabled"])
            self.assertTrue(agents["tester"]["optional"])
            self.assertNotIn("tester", result["architecture"]["stateless"])
            self.assertFalse(config.optional_agent_enabled("tester", config.load_config()))
            for name in ("architect", "explorer", "coder", "verifier"):
                with self.assertRaises(ValueError):
                    config.set_optional_agent_enabled(name, False)
            self.assertTrue(
                json.loads(agent_engine.configure_optional_agent("tester", True))["tester_enabled"]
            )

    def test_disabled_workers_are_never_constructed(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(config, "CONFIG_DIR", Path(directory)),
            patch("protoagent_core.agents.architect.create_selected_llm", return_value=None),
            patch("protoagent_core.agents.coder.create_selected_llm", return_value=None),
            patch("protoagent_core.agents.explorer.create_selected_llm", return_value=None),
            patch("protoagent_core.agents.deck.create_tester_agent") as tester,
            patch("protoagent_core.agents.deck.create_mcp_agent") as mcp,
        ):
            deck = create_agent_deck(transport=None, tester_enabled=False, mcp_enabled=False)
            self.assertEqual(set(deck), {"architect", "explorer", "coder", "verifier"})
            tester.assert_not_called()
            mcp.assert_not_called()
            prompt = architect_system_prompt(tester_enabled=False, mcp_enabled=False)
            self.assertIn("Never delegate to tester", prompt)
            self.assertIn("Never delegate to mcp", prompt)
            self.assertIn("Verifier still runs every required check", prompt)

    def test_selected_model_threads_configuration_into_runtime(self):
        cfg = config.default_config()
        cfg["providers"]["ollama"]["model"] = "fixture"
        cfg["optional_agents"]["tester"]["enabled"] = False
        cfg["optional_agents"]["mcp"]["enabled"] = True
        native_run = AsyncMock(return_value={"answer": "ok"})
        with (
            patch("protoagent_core.runtime.load_config", return_value=cfg),
            patch("protoagent_core.runtime._run_agent_deck", native_run),
        ):
            run_selected_model("hello")
        self.assertFalse(native_run.call_args.kwargs["tester_enabled"])
        self.assertTrue(native_run.call_args.kwargs["mcp_enabled"])
        self.assertEqual(native_run.call_args.kwargs["mcp_config"], cfg)

    def test_server_configuration_is_explicit_and_secret_values_are_not_displayed(self):
        for bad in (
            {"command": "python", "allow_tools": "*"},
            {"transport": "streamable_http", "url": "https://u:pass@example.com/mcp"},
            {"command": "python", "headers": {"Authorization": "secret"}},
            {"command": "python", "read_only_tools": ["hidden"]},
        ):
            with self.assertRaises(ValueError):
                validate_server(bad)
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(config, "CONFIG_DIR", Path(directory)),
            patch.object(config, "CONFIG_PATH", Path(directory) / "config.json"),
        ):
            path = Path(directory) / "server config.json"
            path.write_text(
                json.dumps(
                    {"command": "python", "args": ["secret-argument"], "allow_tools": ["lookup"]}
                )
            )
            text = agent_engine.configure_mcp_text(f'add lookup "{path}"')
            self.assertNotIn("secret-argument", text)
            self.assertNotIn("secret-argument", agent_engine.get_config())
            self.assertFalse(mcp_command([])["enabled"])
            self.assertTrue(mcp_command(["on"])["enabled"])
            self.assertEqual(mcp_command(["remove", "lookup"])["servers"], [])


class MCPRuntimeTests(NativeRuntimeCase):
    def setUp(self):
        super().setUp()
        self.script = self.root / "server.py"
        self.script.write_text(SERVER)
        self.spec = validate_server(
            {
                "command": sys.executable,
                "args": [str(self.script), str(self.root)],
                "allow_tools": ["lookup", "large", "failure", "slow"],
                "read_only_tools": ["lookup"],
            }
        )
        self.mcp = create_mcp_agent(
            servers={"fixture": self.spec},
            transport=None,
            approval_handler=self.broker,
            authorization=self.authorization,
            attempt=self.attempt,
        )
        self.mcp.run_store = self.store

    async def test_denial_never_starts_server_and_broker_never_constructs_model(self):
        self.assertIsNone(self.mcp.llm)
        self.assertEqual(tuple(self.mcp.tools), MCP_TOOLS)
        result = await self.tool(self.mcp, "list_mcp_tools", {"server": "fixture"}, approved=False)
        self.assertFalse((self.root / "sessions").exists())
        self.assertTrue(result.error)
        self.assertFalse(self.mcp.card.capabilities.delegation)

    async def test_real_stdio_discovery_schema_approved_call_and_rich_results(self):
        result = await self.tool(self.mcp, "list_mcp_tools", {"server": "fixture"})
        self.assertFalse(result.error)
        self.assertFalse((self.root / "calls").exists())
        schema = await self.tool(
            self.mcp, "mcp_tool_schema", {"server": "fixture", "tool": "lookup"}
        )
        self.assertFalse(schema.error)
        result = await self.tool(
            self.mcp,
            "call_mcp_tool",
            {"server": "fixture", "tool": "lookup", "arguments": {"key": "hello"}},
        )
        self.assertFalse(result.error)
        self.assertEqual((self.root / "calls").read_text(), "hello\n")
        receipts = [
            event for event in self.handles[-1].report.events if event.type == "action.completed"
        ]
        self.assertTrue(receipts)
        self.assertIn("mcp.invoke", receipts[-1].payload["action"]["capabilities"])
        output = str(result.output)
        self.assertIn("structuredContent", output)
        self.assertIn("found", output)
        self.assertEqual((self.root / "sessions").read_text().count("start"), 3)

    async def test_schema_validation_and_tool_failure_do_not_retry(self):
        invalid = await self.tool(
            self.mcp,
            "call_mcp_tool",
            {"server": "fixture", "tool": "lookup", "arguments": {"key": 123}},
        )
        self.assertTrue(invalid.error)
        self.assertFalse((self.root / "calls").exists())
        failed = await self.tool(
            self.mcp, "call_mcp_tool", {"server": "fixture", "tool": "failure", "arguments": {}}
        )
        self.assertTrue(failed.error)
        self.assertEqual((self.root / "sessions").read_text().count("start"), 2)
        self.assertTrue(self.attempt.has_uncertain_changes())
        repeated = self.start_tool(
            self.mcp, "call_mcp_tool", {"server": "fixture", "tool": "failure", "arguments": {}}
        )
        self.assertTrue((await repeated.result()).error)
        self.assertEqual((self.root / "sessions").read_text().count("start"), 2)
        edit = self.start_tool(
            self.coder,
            "create_file",
            {"path": str(self.root / "after.py"), "content": "unsafe continuation"},
        )
        self.assertTrue((await edit.result()).error)
        self.assertFalse((self.root / "after.py").exists())

    async def test_unallowlisted_calls_and_final_phase_are_denied_before_connection(self):
        task = Task.create_tool_call(
            tool_name="call_mcp_tool", args={"server": "fixture", "tool": "hidden", "arguments": {}}
        )
        self.context.child(agent_name="mcp").attach_to_task(task)
        with self.assertRaises(ActionDeniedError):
            await self.mcp.run_task(task)
        self.assertFalse((self.root / "sessions").exists())
        self.attempt.checking = True
        task = Task.create_tool_call(
            tool_name="call_mcp_tool",
            args={"server": "fixture", "tool": "lookup", "arguments": {"key": "x"}},
        )
        self.context.child(agent_name="mcp").attach_to_task(task)
        with self.assertRaises(ActionDeniedError):
            await self.mcp.run_task(task)
        self.assertFalse((self.root / "sessions").exists())

    async def test_native_result_is_bounded_before_model_handoff(self):
        result = await self.tool(
            self.mcp, "call_mcp_tool", {"server": "fixture", "tool": "large", "arguments": {}}
        )
        self.assertFalse(result.error)
        self.assertIn("truncated", str(result.output))
        self.assertLess(len(str(result.output)), 7000)

    async def test_catalog_paging_and_filtering_do_not_invoke_tools(self):
        access = MCPAccess({"fixture": self.spec})
        first = await access.operate("fixture")
        self.assertEqual(len(first["tools"]), 12)
        self.assertEqual(first["next_offset"], 12)
        last = await access.operate("fixture", offset=12)
        self.assertIsNone(last["next_offset"])
        self.assertEqual(first["total"], len(first["tools"]) + len(last["tools"]))
        match = await access.operate("fixture", query="unused_13")
        self.assertEqual([item["name"] for item in match["tools"]], ["unused_13"])
        self.assertFalse(match["tools"][0]["allowed"])
        self.assertFalse((self.root / "calls").exists())

    async def test_real_sse_and_streamable_http_transports(self):
        for transport, server_transport, endpoint in (
            ("sse", "sse", "sse"),
            ("streamable_http", "streamable-http", "mcp"),
        ):
            with self.subTest(transport=transport):
                with socket.socket() as listener:
                    listener.bind(("127.0.0.1", 0))
                    port = listener.getsockname()[1]
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    str(self.script),
                    str(self.root),
                    server_transport,
                    str(port),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                try:
                    async with asyncio.timeout(5):
                        while True:
                            try:
                                _, writer = await asyncio.open_connection("127.0.0.1", port)
                            except OSError:
                                await asyncio.sleep(0.02)
                            else:
                                writer.close()
                                await writer.wait_closed()
                                break
                    spec = validate_server(
                        {
                            "transport": transport,
                            "url": f"http://127.0.0.1:{port}/{endpoint}",
                            "allow_tools": ["lookup"],
                        }
                    )
                    result = await MCPAccess({"remote": spec}).operate(
                        "remote", tool="lookup", arguments={"key": transport}
                    )
                    self.assertEqual(result["result"]["structuredContent"]["value"], "found")
                finally:
                    if process.returncode is None:
                        process.terminate()
                        try:
                            await asyncio.wait_for(process.wait(), 3)
                        except TimeoutError:
                            process.kill()
                            await process.wait()
        self.assertEqual((self.root / "calls").read_text().splitlines(), ["sse", "streamable_http"])

    async def test_read_only_tasks_deny_undeclared_tool_before_connection(self):
        self.attempt.record.forbids_write = True
        task = Task.create_tool_call(
            tool_name="call_mcp_tool", args={"server": "fixture", "tool": "large", "arguments": {}}
        )
        self.context.child(agent_name="mcp").attach_to_task(task)
        with self.assertRaises(ActionDeniedError):
            await self.mcp.run_task(task)
        self.assertFalse((self.root / "sessions").exists())

    async def test_http_header_environment_values_are_redacted(self):
        cfg = config.default_config()
        cfg["mcp_servers"] = {
            "remote": {
                "transport": "streamable_http",
                "url": "https://example.com/mcp",
                "headers_env": {"Authorization": "FIXTURE_AUTH"},
            }
        }
        config.save_config(cfg)
        with patch.dict(os.environ, {"FIXTURE_AUTH": "custom-private-value"}):
            from protoagent_core.runtime_storage import output_redaction

            self.assertNotIn(
                "custom-private-value",
                str(output_redaction().redact({"text": "custom-private-value"})),
            )
        access = MCPAccess({"remote": validate_server(cfg["mcp_servers"]["remote"])})
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "Missing MCP header"):
                access.adapter("remote")

    async def test_cancellation_closes_owned_stdio_session(self):
        access = MCPAccess({"fixture": self.spec})
        pending = asyncio.create_task(access.operate("fixture", tool="slow", arguments={}))
        async with asyncio.timeout(5):
            while not (self.root / "slow-started").exists():
                await asyncio.sleep(0.02)
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertEqual((self.root / "sessions").read_text().count("start"), 1)

    async def test_architect_routes_mcp_tool_call_with_native_receipts_and_tester_disabled(self):
        from protolink.llms.factory import create_llm

        from protoagent_core.runtime import _run_agent_deck

        llm = create_llm(
            "mock",
            sequential_responses=[
                {
                    "type": "agent_call",
                    "agent": "mcp",
                    "action": "tool_call",
                    "tool": "call_mcp_tool",
                    "args": {
                        "server": "fixture",
                        "tool": "lookup",
                        "arguments": {"key": "reference"},
                    },
                },
                {"type": "final", "content": "Found the requested reference."},
            ],
        )

        async def approve():
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
                await asyncio.sleep(0.01)

        control = asyncio.create_task(approve())
        try:
            with (
                patch.dict(os.environ, {"PROTOAGENT_AGENT_TRANSPORT": "runtime"}),
                patch("protoagent_core.agents.architect.create_selected_llm", return_value=llm),
                patch("protoagent_core.agents.explorer.create_selected_llm", return_value=None),
                patch("protoagent_core.agents.coder.create_selected_llm", return_value=None),
                patch("protoagent_core.agents.deck.create_tester_agent") as tester,
            ):
                result = await asyncio.wait_for(
                    _run_agent_deck(
                        "Look up reference docs",
                        "ollama",
                        "fixture",
                        str(self.root),
                        None,
                        self.bridge,
                        {"resolved": "small", "label": "Small", "configured": "small"},
                        tester_enabled=False,
                        mcp_enabled=True,
                        mcp_config={"mcp_servers": {"fixture": self.spec}},
                    ),
                    10,
                )
                tester.assert_not_called()
        finally:
            control.cancel()
            await asyncio.gather(control, return_exceptions=True)
        self.assertEqual(result["status"], "completed", result["answer"])
        self.assertEqual((self.root / "calls").read_text(), "reference\n")
        self.assertEqual(len(result["approval_decisions"]), 1)
        receipts = [
            event
            for event in result["run_events"]
            if event["type"] == "action.completed"
            and isinstance(event["payload"].get("action"), dict)
            and event["payload"]["action"].get("name") == "call_mcp_tool"
        ]
        self.assertEqual(len(receipts), 1)
        self.assertTrue(receipts[0]["delegation_id"])
        self.assertNotIn("mcp", result["context_admission"])
        self.assertNotIn("tester", result["transport_report"]["agents"])
        self.assertIn("mcp", result["transport_report"]["agents"])

    def test_manifest_and_prompt_explain_no_inference_boundary(self):
        manifest = agent_manifest(mcp_enabled=True, tester_enabled=False)
        self.assertIn("mcp", manifest["architecture"]["stateless"])
        self.assertNotIn("tester", manifest["architecture"]["stateless"])
        prompt = architect_system_prompt(mcp_enabled=True, tester_enabled=False)
        self.assertIn("tool_call, never infer", prompt)
        self.assertIn("Workers request external evidence through you", prompt)
