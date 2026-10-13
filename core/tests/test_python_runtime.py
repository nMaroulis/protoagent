"""Embedded CPython must launch real Python for discovery and coding evals."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from protoagent_core.coding_eval import oracle_result
from protoagent_core.python_runtime import python_executable
from protoagent_core.task_record import discover_checks


class PythonRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def executable(self, relative):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
        path.chmod(0o700)
        return path

    def test_project_environment_keeps_its_symlink_identity(self):
        python = self.root / ".venv/bin/python"
        python.parent.mkdir(parents=True)
        python.symlink_to(sys.executable)
        self.assertEqual(python_executable(self.root), str(python))
        self.assertNotEqual(python_executable(self.root), str(python.resolve()))

    def test_regular_python_preserves_active_interpreter(self):
        self.assertEqual(python_executable(), str(Path(sys.executable).absolute()))

    def test_embedded_host_is_excluded_even_if_it_is_executable(self):
        host = self.executable("host/proto-cli")
        python = self.executable("runtime/bin/python3.14")
        with (
            patch("protoagent_core.python_runtime.sys.executable", str(host)),
            patch("protoagent_core.python_runtime.sys._base_executable", str(host), create=True),
            patch("protoagent_core.python_runtime.sys.prefix", str(self.root / "runtime")),
        ):
            self.assertEqual(python_executable(self.root), str(python))
            (self.root / "sample.py").write_text("value = 42\n")
            check = discover_checks(str(self.root))["python-tests"]
            self.assertEqual(check.argv[0], str(python))
            self.assertEqual(check.env["PATH"].split(os.pathsep)[0], str(python.parent))
            self.assertNotIn(str(host.parent), check.env["PATH"].split(os.pathsep))

    def test_embedded_oracle_uses_external_python(self):
        with patch("protoagent_core.python_runtime.sys.executable", "/path/to/proto-cli"):
            result = oracle_result(self.root, "assert 2 + 2 == 4\n")
        self.assertTrue(result["passed"], result)

    def test_embedded_host_retains_injected_dependency_environment(self):
        host = self.executable("host/proto-cli")
        bare = self.executable("base/bin/python3.14")
        python = self.executable("sdk/.venv/bin/python")
        (python.parent.parent / "pyvenv.cfg").write_text("home = base\n")
        site = python.parent.parent / "lib/python3.14/site-packages"
        site.mkdir(parents=True)
        with (
            patch.dict(os.environ, {"VIRTUAL_ENV": ""}),
            patch("protoagent_core.python_runtime.sys.executable", str(host)),
            patch("protoagent_core.python_runtime.sys._base_executable", str(bare), create=True),
            patch("protoagent_core.python_runtime.sys.prefix", str(bare.parent.parent)),
            patch("protoagent_core.python_runtime.sys.base_prefix", str(bare.parent.parent)),
            patch("protoagent_core.python_runtime.sys.path", [str(site)]),
        ):
            self.assertEqual(python_executable(self.root), str(python))
            project = self.executable(".venv/bin/python")
            self.assertEqual(python_executable(self.root), str(project))

    def test_embedded_host_prefers_explicit_active_environment(self):
        python = self.executable("active/bin/python")
        with (
            patch.dict(os.environ, {"VIRTUAL_ENV": str(python.parent.parent)}),
            patch("protoagent_core.python_runtime.sys.executable", "/path/to/proto-cli"),
        ):
            self.assertEqual(python_executable(self.root), str(python))

    def test_javascript_and_explicit_checks_do_not_need_python_resolution(self):
        with patch("protoagent_core.task_record.python_executable", side_effect=AssertionError):
            (self.root / "package.json").write_text('{"scripts":{}}')
            self.assertEqual(discover_checks(str(self.root)), {})
            (self.root / ".protoagent").mkdir()
            (self.root / ".protoagent/project.json").write_text('{"checks":[]}')
            (self.root / "sample.py").write_text("value = 42\n")
            self.assertEqual(discover_checks(str(self.root)), {})

    def test_missing_interpreter_fails_without_running_the_host_binary(self):
        with (
            patch("protoagent_core.python_runtime.sys.executable", "/path/to/proto-cli"),
            patch("protoagent_core.python_runtime.sys._base_executable", "", create=True),
            patch("protoagent_core.python_runtime.Path.is_file", return_value=False),
            patch("protoagent_core.python_runtime.shutil.which", return_value=None),
        ):
            with self.assertRaisesRegex(RuntimeError, "No external Python interpreter"):
                python_executable()
