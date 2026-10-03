"""Console diagnostics cannot bypass the embedded frontend's renderer."""

import json
import logging
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from protoagent_core.runtime_bridge import RuntimeBridge
from protoagent_core.runtime_storage import output_redaction


class RuntimeConsoleTests(unittest.TestCase):
    def test_prebound_and_new_logging_handlers_are_captured_and_restored(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = RuntimeBridge(str(Path(directory) / "progress.jsonl"))
            console = StringIO()
            logger = logging.getLogger("protoagent-test-console")
            logger.propagate = False
            existing = logging.StreamHandler(console)
            file_handler = logging.FileHandler(Path(directory) / "durable.log")
            logger.addHandler(existing)
            logger.addHandler(file_handler)
            try:
                with redirect_stdout(console):
                    with self.assertRaisesRegex(ValueError, "fixture failure"):
                        with bridge.capture_console():
                            added = logging.StreamHandler(sys.stderr)
                            logger.addHandler(added)
                            logger.error("\x1b[2Jprovider failed")
                            raise ValueError("fixture failure")
                    self.assertIs(existing.stream, console)
                    self.assertIs(added.stream, sys.stderr)
                self.assertEqual(console.getvalue(), "")
                records = [
                    json.loads(line) for line in bridge.progress_path.read_text().splitlines()
                ]
                self.assertEqual(len(records), 2)
                self.assertTrue(all("provider failed" in r["live_output"]["text"] for r in records))
                self.assertTrue(all("\x1b" not in r["live_output"]["text"] for r in records))
                file_handler.flush()
                self.assertIn("provider failed", (Path(directory) / "durable.log").read_text())
            finally:
                for handler in list(logger.handlers):
                    logger.removeHandler(handler)
                    handler.close()

    def test_console_output_is_captured_redacted_and_restored_after_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = RuntimeBridge(str(Path(directory) / "progress.jsonl"))
            bridge.redaction = output_redaction("split-secret")
            stdout, stderr = StringIO(), StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                with self.assertRaisesRegex(ValueError, "fixture failure"):
                    with bridge.capture_console():
                        print("\x1b[2J\x1b[Hprovider startup", end="")
                        print("split-", end="", file=sys.stderr)
                        print("secret", file=sys.stderr)
                        raise ValueError("fixture failure")
                self.assertIs(sys.stdout, stdout)
                self.assertIs(sys.stderr, stderr)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(stderr.getvalue(), "")
            records = [json.loads(line) for line in bridge.progress_path.read_text().splitlines()]
            self.assertEqual([r["live_output"]["channel"] for r in records], ["stdout", "stderr"])
            self.assertEqual(records[0]["live_output"]["text"], "provider startup")
            self.assertIn("[REDACTED]", records[1]["live_output"]["text"])
            self.assertNotIn("split-secret", bridge.progress_path.read_text())

    def test_console_capture_is_bounded_and_masks_a_truncated_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = RuntimeBridge(str(Path(directory) / "progress.jsonl"))
            bridge.redaction = output_redaction("split-secret")
            with bridge.capture_console():
                written = sys.stdout.write("x" * (32_768 - len("split-")) + "split-secret")
            self.assertEqual(written, 32_774)
            record = json.loads(bridge.progress_path.read_text())
            text = record["live_output"]["text"]
            self.assertNotIn("split-", text)
            self.assertTrue(text.endswith("[REDACTED]\n[Console diagnostics truncated]"))
            self.assertLess(len(text), 32_850)

    def test_calls_without_a_frontend_bridge_preserve_console_output(self):
        stdout = StringIO()
        with redirect_stdout(stdout), RuntimeBridge(None).capture_console():
            print("direct core caller")
        self.assertEqual(stdout.getvalue(), "direct core caller\n")

    def test_subprocess_sdk_can_use_the_captured_stderr_descriptor(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = RuntimeBridge(str(Path(directory) / "progress.jsonl"))
            with bridge.capture_console():
                subprocess.run(
                    [sys.executable, "-c", "import sys; print('server startup', file=sys.stderr)"],
                    stdout=subprocess.DEVNULL,
                    stderr=sys.stderr,
                    check=True,
                )
            record = json.loads(bridge.progress_path.read_text())
            self.assertEqual(record["live_output"]["channel"], "stderr")
            self.assertEqual(record["live_output"]["text"], "server startup\n")


if __name__ == "__main__":
    unittest.main()
