"""Project commands are prepared without requiring models to spell host paths."""

import sys
from pathlib import Path

from runtime_support import NativeRuntimeCase


class CommandPreparationTests(NativeRuntimeCase):
    async def test_screenshot_mkdir_uses_project_cwd_empty_env_and_valid_default_limits(self):
        result = await self.tool(
            self.verifier, "execute_command", {"argv": ["mkdir", "test"], "cwd": ".", "env": {}}
        )
        self.assertEqual(result.status, "completed", result.error)
        self.assertTrue((self.root / "test").is_dir())
        self.assertFalse(self.attempt.checking)
        (approval,) = self.broker.records(self.authorization.scope)
        args = approval.request.action.payload["arguments"]
        self.assertTrue(Path(args["argv"][0]).is_absolute())
        self.assertEqual(Path(args["argv"][0]).name, "mkdir")
        self.assertEqual(args["cwd"], str(self.root))
        self.assertEqual(args["env"], {})
        self.assertEqual(args["max_output_bytes"], 8192)
        preview = approval.request.action.artifacts[0].parts[0].content
        self.assertEqual(preview["argv"], args["argv"])
        self.assertEqual(preview["cwd"], str(self.root))
        self.assertFalse(preview["implicit_shell"])

    async def test_python_alias_retains_project_venv_identity(self):
        python = self.root / ".venv/bin/python"
        python.parent.mkdir(parents=True)
        python.symlink_to(sys.executable)
        result = await self.tool(
            self.verifier,
            "execute_command",
            {"argv": ["python3", "-c", "print('project-python')"]},
        )
        self.assertEqual(result.status, "completed", result.error)
        (approval,) = self.broker.records(self.authorization.scope)
        self.assertEqual(approval.request.action.payload["arguments"]["argv"][0], str(python))
        self.assertIn("project-python", str(result.output))

    async def test_explicit_path_lookup_is_relative_to_project_cwd(self):
        (self.root / "bin").mkdir()
        program = self.root / "bin/project-helper"
        program.write_text("#!/bin/sh\nprintf 'helper executed\\n'\n")
        program.chmod(0o700)
        result = await self.tool(
            self.verifier,
            "execute_command",
            {"argv": ["project-helper"], "env": {"PATH": "bin"}},
        )
        self.assertEqual(result.status, "completed", result.error)
        (approval,) = self.broker.records(self.authorization.scope)
        args = approval.request.action.payload["arguments"]
        self.assertEqual(args["argv"], [str(program)])
        self.assertEqual(args["env"], {"PATH": "bin"})
        self.assertIn("helper executed", str(result.output))

    async def test_outside_project_cwd_still_requires_native_policy_denial(self):
        result = await self.start_tool(
            self.verifier,
            "execute_command",
            {"argv": [sys.executable, "-c", "print('must not run')"], "cwd": str(self.root.parent)},
        ).result()
        self.assertEqual(result.status, "failed")
        self.assertTrue(self.attempt.denied)
        self.assertFalse(self.broker.records(self.authorization.scope))
        self.assertFalse(any(e.type == "process.finished" for e in result.report.events))

    async def test_empty_env_does_not_copy_parent_secrets_or_path(self):
        result = await self.tool(
            self.verifier,
            "execute_command",
            {"argv": [sys.executable, "-c", "import os; print(sorted(os.environ))"], "env": {}},
        )
        self.assertEqual(result.status, "completed", result.error)
        (approval,) = self.broker.records(self.authorization.scope)
        self.assertEqual(approval.request.action.payload["arguments"]["env"], {})
        self.assertNotIn("PATH", str(result.output))
        self.assertNotIn("HOME", str(result.output))
