"""Version metadata contract tests."""

from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path

from protoagent_core import __version__
from protoagent_core.agent_engine import component_versions


class VersioningTests(unittest.TestCase):
    def test_core_package_metadata_matches_runtime_version(self) -> None:
        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        metadata = tomllib.loads(pyproject.read_text(encoding="utf-8"))

        self.assertEqual(metadata["project"]["version"], __version__)
        self.assertEqual(__version__, "0.1.0")

    def test_component_inventory_covers_cli_core_and_acp(self) -> None:
        data = json.loads(component_versions("0.1.0"))
        components = {item["id"]: item for item in data["components"]}

        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(components["cli"]["version"], "0.1.0")
        self.assertEqual(components["core"]["version"], "0.1.0")
        self.assertEqual(components["acp"]["version"], "0.0.0-dev.0")
        self.assertEqual(components["acp"]["status"], "planned")
