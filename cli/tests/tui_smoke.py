"""Real-terminal regressions, using isolated settings and no external model.

Run with the repo venv after cargo build --release:
    .venv/bin/python cli/tests/tui_smoke.py
"""

import codecs
import fcntl
import json
import os
import pty
import re
import select
import signal
import struct
import subprocess
import sysconfig
import tempfile
import termios
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BINARY = Path(os.environ.get("PROTOAGENT_TEST_CLI", str(ROOT / "cli/target/release/proto-cli")))
CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
MOCK = """
import asyncio, logging, os, sys
from pathlib import Path
import protoagent_core.models as models
import protolink.llms.factory as factory
from protolink.llms.mock_client import MockLLM
models.model_startup_problem = lambda: None
logger = logging.getLogger('tui-smoke-prebound')
logger.propagate = False
logger.addHandler(logging.StreamHandler(sys.stdout))
class SlowMock(MockLLM):
    async def call_stream(self, history):
        logger.error('\\x1b[2Jprebound provider diagnostic')
        print('\\x1b[2Jpython provider diagnostic', file=sys.stderr)
        Path(os.environ['PROTOAGENT_CONFIG_DIR'], 'model-started').touch()
        await asyncio.sleep(1.0)
        response = self.call(history)
        for offset in range(0, len(response), 8):
            await asyncio.sleep(0.04)
            yield response[offset:offset + 8]
factory.create_llm = lambda *args, **kwargs: SlowMock(default_response='Fixture answer complete.')
"""

ACTIVITY_MOCK = r"""
import asyncio, os
from pathlib import Path
import protoagent_core.models as models
import protoagent_core.agent_engine as engine
import protoagent_core.agents.explorer as explorer
import protoagent_core.agents.deck as deck
import protolink.llms.factory as factory
from protolink.llms.mock_client import MockLLM
models.model_startup_problem = lambda: None
engine.model_startup_problem = lambda: None
create_explorer = explorer.create_explorer_agent
def slow_explorer(*args, **kwargs):
    agent = create_explorer(*args, **kwargs)
    original = agent.tools['read_file']
    @agent.tool(capabilities=['workspace.read'])
    async def read_file(path: str, start_line: int = 1, end_line: int | None = None) -> dict:
        await asyncio.sleep(0.8)
        return await original(path=path, start_line=start_line, end_line=end_line)
    return agent
explorer.create_explorer_agent = slow_explorer
deck.create_explorer_agent = slow_explorer
class ActivityMock(MockLLM):
    @property
    def supports_native_action_stream(self):
        # Reproduce native-capable providers sending JSON actions as text.
        return True
    async def call_stream(self, history):
        await asyncio.sleep(0.8)
        response = self.call(history)
        for offset in range(0, len(response), 8):
            await asyncio.sleep(0.04)
            yield response[offset:offset + 8]
factory.create_llm = lambda *args, **kwargs: ActivityMock(sequential_responses=[
    {'type':'tool_call','tool':'read_file','args':{'path':'README.md'}},
    {'type':'final','content':'Fixture answer complete.'},
])
"""

APPROVAL_MOCK = """
import os
from pathlib import Path
import protoagent_core.models as models
import protoagent_core.agent_engine as engine
import protolink.llms.factory as factory
from protolink.llms.mock_client import MockLLM
models.model_startup_problem = lambda: None
engine.model_startup_problem = lambda: None
project = Path(os.environ['PROTOAGENT_TUI_FIXTURE_PROJECT'])
counts = {}
def respond(history, prompt):
    role = 'coder' if 'Your registered name is coder.' in history.to_list()[0]['content'] else 'architect'
    step = counts.get(role, 0)
    counts[role] = step + 1
    if step:
        return {'type':'final', 'content':'Fixture action finished.'}
    if role == 'coder':
        return {'type':'tool_call','tool':'replace_file','args':{'path':str(project/'sample.py'),'content':'value = 42\\n'}}
    if os.environ.get('PROTOAGENT_TUI_FIXTURE_DIFF') == '1':
        return {'type':'tool_call','tool':'coder','args':{'task':'Change sample.py value to 42.'}}
    return {'type':'tool_call','tool':'run_check','args':{'check_id':'python-tests','phase':'baseline'}}
factory.create_llm = lambda *args, **kwargs: MockLLM(response_callback=respond)
"""

HANDOFF_MOCK = r"""
import json, os
from pathlib import Path
import protoagent_core.models as models
import protoagent_core.agent_engine as engine
import protolink.llms.factory as factory
from protolink.llms.mock_client import MockLLM
models.model_startup_problem = lambda: None
engine.model_startup_problem = lambda: None
project = Path(os.environ['PROTOAGENT_TUI_FIXTURE_PROJECT'])
counts = {}
def respond(history, prompt):
    role = 'coder' if 'Your registered name is coder.' in history.to_list()[0]['content'] else 'architect'
    step = counts.get(role, 0)
    counts[role] = step + 1
    Path(os.environ['PROTOAGENT_CONFIG_DIR'], 'model-calls.json').write_text(json.dumps(counts))
    if role == 'coder':
        if step == 0:
            return {'type':'tool_call','tool':'replace_file','args':{'path':str(project/'sample.py'),'content':'value = 42\n'}}
        return {'type':'final','content':'Updated sample.py.'}
    if step == 0:
        return {'type':'tool_call','tool':'coder','args':{'task':'Change sample.py value to 42.'}}
    if step == 1:
        return {'type':'tool_call','tool':'run_check','args':{'check_id':'regression','phase':'verify'}}
    if step == 2:
        return {'type':'tool_call','tool':'ask_user','args':{'question':'Regression failed. Retry or defer verification?','options':['Install', 'Retry']}}
    raise RuntimeError('Manual handoff incorrectly continued model inference')
factory.create_llm = lambda *args, **kwargs: MockLLM(response_callback=respond)
"""


class Screen:
    """Interpret the cursor/clear commands used by the renderer for visible QA."""

    def __init__(self, width=100, height=30):
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.pending = ""
        self.resize(width, height)

    def resize(self, width, height):
        self.width, self.height = width, height
        self.cells = [[" "] * width for _ in range(height)]
        self.row = self.column = 0

    def feed(self, data):
        self.pending += self.decoder.decode(data)
        index = 0
        while index < len(self.pending):
            if self.pending.startswith("\x1b[", index):
                match = CSI.match(self.pending, index)
                if not match:
                    break
                command = match.group()
                if command.endswith("H"):
                    position = command[2:-1].split(";")
                    self.row = int(position[0] or 1) - 1
                    self.column = int(position[1] or 1) - 1 if len(position) > 1 else 0
                elif command == "\x1b[2J":
                    self.cells = [[" "] * self.width for _ in range(self.height)]
                index = match.end()
            elif self.pending.startswith("\x1b]", index):
                end = self.pending.find("\x07", index)
                if end < 0:
                    break
                index = end + 1
            elif self.pending[index] == "\x1b":
                break
            else:
                character = self.pending[index]
                if (
                    character >= " "
                    and 0 <= self.row < self.height
                    and 0 <= self.column < self.width
                ):
                    self.cells[self.row][self.column] = character
                    self.column += 1
                index += 1
        self.pending = self.pending[index:]

    def text(self):
        return "\n".join("".join(row) for row in self.cells)


class Terminal:
    def __init__(self, fixture="offline"):
        self.temp = tempfile.TemporaryDirectory(prefix="protoagent-tui-test-")
        self.directory = Path(self.temp.name) / "state"
        self.directory.mkdir()
        self.project = (Path(self.temp.name) / "workspace").resolve()
        self.project.mkdir()
        (self.directory / "config.json").write_text(
            json.dumps(
                {
                    "active_provider": "ollama",
                    "providers": {
                        "ollama": {
                            "model": "fixture-small",
                            "base_url": "http://127.0.0.1:9",
                        }
                    },
                }
            )
        )
        (self.directory / "project.json").write_text(
            json.dumps({"active_project": str(self.project)})
        )
        if fixture == "mock":
            (self.directory / "sitecustomize.py").write_text(MOCK)
        elif fixture == "activity":
            (self.directory / "sitecustomize.py").write_text(ACTIVITY_MOCK)
            (self.project / "README.md").write_text("A project for tracking widgets.\n")
        elif fixture == "broken":
            (self.directory / "sitecustomize.py").write_text(
                MOCK
                + "\ndef broken(*args, **kwargs): raise RuntimeError('Fixture startup failure')\nfactory.create_llm = broken\n"
            )
        elif fixture == "discovery":
            (self.directory / "sitecustomize.py").write_text(
                "import time\nimport protoagent_core.agent_engine as engine\ndef slow(*args):\n time.sleep(1)\n return '{}'\nengine.list_models = slow\nengine.doctor = slow\n"
            )
        elif fixture == "malformed":
            (self.directory / "sitecustomize.py").write_text(
                "import protoagent_core.agent_engine as engine\nengine.answer_help_question = lambda *args: '{invalid JSON}'\n"
            )
        elif fixture.startswith("approval"):
            (self.directory / "sitecustomize.py").write_text(APPROVAL_MOCK)
            if fixture == "approval-diff-expired":
                (self.project / "sample.py").write_text("value = 0\n")
            else:
                (self.project / "test_sample.py").write_text(
                    "import unittest\nfrom pathlib import Path\nclass Regression(unittest.TestCase):\n"
                    "    def test_command(self):\n        Path(__file__).with_name('check-executed').write_text('ran')\n"
                    "        self.assertTrue(True)\n"
                )
        elif fixture in {"handoff", "unavailable"}:
            (self.directory / "sitecustomize.py").write_text(HANDOFF_MOCK)
            (self.project / "sample.py").write_text("value = 0\n")
            (self.project / ".protoagent").mkdir()
            (self.project / ".protoagent/project.json").write_text(
                json.dumps(
                    {
                        "checks": [
                            {
                                "id": "regression",
                                "argv": [
                                    str(ROOT / ".venv/bin/python"),
                                    "-c",
                                    "raise ModuleNotFoundError(\"No module named 'pytest'\")"
                                    if fixture == "unavailable"
                                    else 'raise AssertionError("Regression failed")',
                                ],
                                "env": {},
                                "paths": ["sample.py"],
                            }
                        ]
                    }
                )
            )
        environment = os.environ.copy()
        environment.update(
            {
                "PROTOAGENT_CONFIG_DIR": str(self.directory),
                "PYTHONPATH": os.pathsep.join(
                    [
                        str(self.directory),
                        str(ROOT / "core"),
                        sysconfig.get_paths()["purelib"],
                    ]
                ),
                "TERM": "xterm-256color",
                "PROTOAGENT_TUI_FIXTURE_PROJECT": str(self.project),
                "PROTOAGENT_TUI_FIXTURE_DIFF": "1" if fixture == "approval-diff-expired" else "0",
            }
        )
        if fixture.endswith("expired"):
            environment["PROTOAGENT_RUN_MAX_SECONDS"] = "1.5"
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
        self.screen = Screen()
        self.data = bytearray()
        self.started = time.monotonic()
        self.child = subprocess.Popen(
            [str(BINARY), "start"],
            cwd=ROOT,
            env=environment,
            stdin=slave,
            stdout=slave,
            stderr=slave,
        )
        os.close(slave)
        self.wait_for("Ask anything")
        self.startup_ms = round((time.monotonic() - self.started) * 1000)

    def pump(self, timeout=0.05):
        if select.select([self.master], [], [], timeout)[0]:
            data = os.read(self.master, 65_536)
            self.data.extend(data)
            self.screen.feed(data)

    def wait_for(self, text, after=0, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if text in CSI.sub("", self.data[after:].decode("utf-8", "replace")):
                return
            self.pump()
        raise AssertionError(
            f"Timed out waiting for {text!r}. Visible screen:\n{self.screen.text()}"
        )

    def send(self, text):
        offset = len(self.data)
        os.write(self.master, text.encode())
        return offset

    def command(self, text, expected):
        offset = self.send(text + "\r")
        self.wait_for(expected, after=offset)
        # An answer may be visible before its stream/cleanup completes. Wait
        # for the idle composer before issuing the next ordinary command.
        self.wait_for("Ask anything", after=offset)

    def resize(self, width, height):
        self.screen.resize(width, height)
        offset = len(self.data)
        fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", height, width, 0, 0))
        os.kill(self.child.pid, signal.SIGWINCH)
        return offset

    def close(self):
        self.child.terminate()
        try:
            self.child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.child.kill()
            self.child.wait()
        os.close(self.master)
        self.temp.cleanup()


@unittest.skipUnless(BINARY.is_file(), "Build the release CLI first")
class TuiSmokeTests(unittest.TestCase):
    def test_loading_activity_replaces_itself_and_disappears_when_answer_streams(self):
        terminal = self.terminal("activity")
        offset = terminal.send("What's this project about?\r")
        terminal.wait_for("Architect: Planning the next step...", after=offset)
        terminal.wait_for("Explorer: Reading README.md...", after=offset)
        self.assertIn("Explorer: Reading README.md...", terminal.screen.text())
        self.assertNotIn("Architect: Planning the next step...", terminal.screen.text())
        next_phase = len(terminal.data)
        terminal.wait_for("Architect: Planning the next step...", after=next_phase)
        self.assertNotIn("Explorer: Reading README.md...", terminal.screen.text())
        terminal.wait_for("Fixture answer complete.", after=next_phase)
        terminal.wait_for("Ask anything", after=next_phase)
        self.assertNotIn("Planning the next step", terminal.screen.text())
        self.assertNotIn("Reading README", terminal.screen.text())
        transcript = CSI.sub("", terminal.data[offset:].decode("utf-8", "replace"))
        self.assertNotIn('"type":"tool_call"', transcript)
        self.assertNotIn('"type": "tool_call"', transcript)
        session = json.loads((terminal.directory / "sessions.json").read_text())
        self.assertEqual(
            session["sessions"][0]["history"][-1]["answer_preview"], "Fixture answer complete."
        )
        self.assertNotIn("Planning the next step", json.dumps(session))
        self.assert_intact(terminal)

    def test_free_text_manual_verification_finishes_with_applied_diff_and_failed_evidence(self):
        terminal = self.terminal("handoff")
        start = terminal.send("Change sample.py so value is 42\r")
        terminal.wait_for("Apply file change?", after=start)
        offset = terminal.send("v")
        terminal.wait_for("File change", after=offset)
        offset = terminal.send("y")
        terminal.wait_for("Run verification check?", after=offset)
        self.assertEqual((terminal.project / "sample.py").read_text(), "value = 42\n")
        offset = terminal.send("y")
        terminal.wait_for("Architect has a question", after=offset)
        offset = terminal.send("do not run it its ok Ill do it\r")
        terminal.wait_for("Verification: deferred to you.", after=offset)
        terminal.wait_for("Ask anything", after=offset)
        terminal.wait_for("Changes applied: sample.py.", after=offset)
        counts = json.loads((terminal.directory / "model-calls.json").read_text())
        self.assertEqual(counts, {"architect": 3, "coder": 2})
        self.assert_intact(terminal)

    def test_auto_edits_skip_file_approval_and_finish_with_applied_review(self):
        terminal = self.terminal("handoff")
        terminal.command("/approvals edits", "Approvals: edits")
        modes = json.loads((terminal.directory / "config.json").read_text())["approval_modes"]
        self.assertEqual(modes[str(terminal.project.resolve())], "edits")
        start = terminal.send("Change sample.py so value is 42\r")
        terminal.wait_for("Run verification check?", after=start)
        self.assertNotIn("Apply file change?", terminal.data[start:].decode(errors="replace"))
        self.assertEqual((terminal.project / "sample.py").read_text(), "value = 42\n")
        offset = terminal.send("y")
        terminal.wait_for("Architect has a question", after=offset)
        offset = terminal.send("do not run it its ok Ill do it\r")
        terminal.wait_for("Edits: 1 file", after=offset)
        terminal.wait_for("Ask anything", after=offset)
        offset = terminal.send("/diff\r")
        terminal.wait_for("Applied Diff Review", after=offset)
        terminal.wait_for("value = 0", after=offset)
        terminal.wait_for("value = 42", after=offset)
        offset = terminal.send("\x1b")
        terminal.wait_for("Ask anything", after=offset)
        terminal.command("/approvals ask", "Approvals: ask")
        self.assert_intact(terminal)

    def test_missing_pytest_is_compact_without_install_question_or_repair(self):
        terminal = self.terminal("unavailable")
        terminal.command("/approvals auto", "Approvals: auto")
        start = terminal.send("Change sample.py so value is 42\r")
        terminal.wait_for("Verification: unavailable (missing pytest).", after=start)
        terminal.wait_for("Edits: 1 file", after=start)
        terminal.wait_for("Ask anything", after=start)
        output = terminal.data[start:].decode(errors="replace")
        self.assertNotIn("Architect has a question", output)
        self.assertNotIn("raise ModuleNotFoundError", output)
        counts = json.loads((terminal.directory / "model-calls.json").read_text())
        self.assertEqual(counts, {"architect": 2, "coder": 2})
        self.assertEqual((terminal.project / "sample.py").read_text(), "value = 42\n")
        self.assertRegex(output, r"\x1b\[38;2;116;223;159m(?:\x1b\[[0-9;]*m)*\+1")
        self.assertRegex(output, r"\x1b\[38;2;255;122;138m(?:\x1b\[[0-9;]*m)*-1")
        self.assert_intact(terminal)

    def test_approval_picker_is_offline_and_settings_persist(self):
        terminal = self.terminal("offline")
        offset = terminal.send("/approvals\r")
        terminal.wait_for("Project approvals", after=offset)
        offset = terminal.send("\x1b[B\r")
        terminal.wait_for("Approvals: edits", after=offset)
        terminal.wait_for("Ask anything", after=offset)
        terminal.command("/approvals status", "Approvals: edits")
        terminal.command("/approvals ask", "Approvals: ask")
        self.assert_intact(terminal)

    def test_command_details_require_an_explicit_decision(self):
        terminal = self.terminal("approval-command")
        start = terminal.send("Explain this project and run its baseline\r")
        terminal.wait_for("Run baseline check?", after=start)
        offset = terminal.send("v")
        terminal.wait_for("Command details", after=offset)
        terminal.wait_for("Viewing details has not approved", after=offset)
        self.assertFalse((terminal.project / "check-executed").exists())
        offset = terminal.send("\r")
        terminal.wait_for("Run baseline check?", after=offset)
        self.assertFalse((terminal.project / "check-executed").exists())
        offset = terminal.send("v")
        terminal.wait_for("Command details", after=offset)
        offset = terminal.send("y")
        terminal.wait_for("Ask anything", after=offset)
        self.assertTrue((terminal.project / "check-executed").exists())

    def test_command_details_close_when_native_approval_wait_expires(self):
        terminal = self.terminal("approval-command-expired")
        start = terminal.send("Explain this project and run its baseline\r")
        terminal.wait_for("Run baseline check?", after=start)
        offset = terminal.send("v")
        terminal.wait_for("Command details", after=offset)
        terminal.wait_for("waiting for your approval", after=offset)
        terminal.wait_for("Ask anything", after=offset)
        self.assertFalse((terminal.project / "check-executed").exists())

    def test_file_diff_closes_when_native_approval_wait_expires(self):
        terminal = self.terminal("approval-diff-expired")
        start = terminal.send("Change sample.py so value is 42\r")
        terminal.wait_for("Apply file change?", after=start)
        offset = terminal.send("v")
        terminal.wait_for("File change", after=offset)
        terminal.wait_for("Ask anything", after=offset)
        self.assertEqual((terminal.project / "sample.py").read_text(), "value = 0\n")

    def terminal(self, fixture="offline"):
        terminal = Terminal(fixture)
        self.addCleanup(terminal.close)
        return terminal

    def assert_intact(self, terminal):
        deadline = time.monotonic() + 1
        while "PROJECT" not in terminal.screen.text() and time.monotonic() < deadline:
            terminal.pump()
        self.assertIsNone(terminal.child.poll())
        self.assertEqual(terminal.data.count(b"\x1b[?1049l"), 0)
        self.assertEqual(terminal.data.count(b"\x1b[2J"), 1)
        self.assertNotIn(b"\n", terminal.data)
        self.assertIn("PROTOAGENT TERMINAL", terminal.screen.text())
        self.assertIn("PROJECT", terminal.screen.text())

    def test_offline_help_preserves_ui_and_agent_mcp_controls_still_work(self):
        terminal = self.terminal()
        terminal.command("/help how to configure agents?", "Cannot reach")
        self.assert_intact(terminal)
        terminal.command("/agents tester off", "tester: off")
        self.assertFalse(
            json.loads((terminal.directory / "config.json").read_text())["optional_agents"][
                "tester"
            ]["enabled"]
        )
        terminal.command("/mcp remove missing", "Unknown MCP server: missing")
        self.assert_intact(terminal)
        print(f"\nOffline first interactive frame: {terminal.startup_ms} ms")

    def test_prebound_logger_and_console_controls_cannot_clear_streaming_ui(self):
        terminal = self.terminal("mock")
        terminal.command("/help fixture question", "Fixture answer complete.")
        self.assert_intact(terminal)
        terminal.command("/trace", "prebound provider diagnostic")
        self.assert_intact(terminal)

    def test_provider_startup_failure_is_inline_and_next_command_works(self):
        terminal = self.terminal("broken")
        terminal.command("/help fixture question", "Guide could not answer")
        self.assert_intact(terminal)
        terminal.command("/config", "Config panel pinned.")
        self.assert_intact(terminal)

    def test_malformed_backend_response_preserves_ui_and_next_command(self):
        terminal = self.terminal("malformed")
        terminal.command("/help fixture question", "The terminal is still ready")
        self.assert_intact(terminal)
        terminal.command("/config", "Config panel pinned.")
        self.assert_intact(terminal)

    def test_scroll_resize_redraw_and_cancel_during_generation(self):
        terminal = self.terminal("mock")
        terminal.command("/agents", "Agents panel pinned.")
        terminal.send("/help fixture question\r")
        deadline = time.monotonic() + 3
        while not (terminal.directory / "model-started").exists() and time.monotonic() < deadline:
            terminal.pump()
        offset = terminal.send("\x1b[5~")  # PageUp
        terminal.wait_for("CHAT SCROLLED", after=offset)
        offset = terminal.resize(80, 24)
        terminal.wait_for("PROTOAGENT TERMINAL", after=offset)
        terminal.send("\x0c")  # Ctrl-L redraw
        terminal.send("\x03")  # Ctrl-C cancel
        terminal.wait_for("canceled", after=offset)
        self.assert_intact(terminal)
        terminal.command("/config", "Config panel pinned.")

    def test_tiny_resize_is_safe_and_restores_ui(self):
        terminal = self.terminal()
        offset = terminal.resize(30, 8)
        terminal.wait_for("resize terminal", after=offset)
        offset = terminal.resize(100, 30)
        terminal.wait_for("PROTOAGENT TERMINAL", after=offset)
        self.assert_intact(terminal)

    def test_slow_discovery_and_doctor_can_be_canceled(self):
        terminal = self.terminal("discovery")
        terminal.command("/models", "Loading Models")
        terminal.command("\x03", "Model discovery canceled.")
        terminal.command("/config", "Config panel pinned.")
        terminal.command("/check", "checking runtime")
        terminal.command("\x03", "Runtime check canceled.")
        self.assert_intact(terminal)


if __name__ == "__main__":
    unittest.main(verbosity=2)
