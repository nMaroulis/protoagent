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
BINARY = ROOT / "cli/target/release/proto-cli"
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
        self.directory = Path(self.temp.name)
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
            json.dumps({"active_project": str(self.directory)})
        )
        if fixture == "mock":
            (self.directory / "sitecustomize.py").write_text(MOCK)
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
            }
        )
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
        fcntl.ioctl(
            self.master, termios.TIOCSWINSZ, struct.pack("HHHH", height, width, 0, 0)
        )
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
            json.loads((terminal.directory / "config.json").read_text())[
                "optional_agents"
            ]["tester"]["enabled"]
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
        while (
            not (terminal.directory / "model-started").exists()
            and time.monotonic() < deadline
        ):
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
