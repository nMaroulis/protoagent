from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from protoagent_core.context import context_status
from protoagent_core.context.indexer import refresh_context_index
from protoagent_core.context.store import ContextStore
from protoagent_core.tools import build_context_map


class ContextIndexerTests(unittest.TestCase):
    def test_context_map_walk_is_stable_for_small_model_prompts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "zeta").mkdir()
            (root / "alpha").mkdir()
            (root / "zeta" / "b.py").write_text("b = 1\n", encoding="utf-8")
            (root / "alpha" / "z.py").write_text("z = 1\n", encoding="utf-8")
            (root / "alpha" / "a.py").write_text("a = 1\n", encoding="utf-8")

            paths = [item["path"] for item in build_context_map(str(root))["files"]]

        self.assertEqual(paths, ["alpha/a.py", "alpha/z.py", "zeta/b.py"])

    def test_refresh_skips_unchanged_files_and_removes_deleted_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            config_dir = root / "config"
            workspace.mkdir()
            first = workspace / "first.py"
            second = workspace / "second.md"
            first.write_text("value = 1\n", encoding="utf-8")
            second.write_text("# Notes\n", encoding="utf-8")

            with patch.dict(
                os.environ,
                {"PROTOAGENT_CONFIG_DIR": str(config_dir)},
                clear=False,
            ):
                initial = refresh_context_index(str(workspace))
                unchanged = refresh_context_index(str(workspace))
                persisted = context_status(str(workspace))

                first.write_text("value = 200\n", encoding="utf-8")
                changed = refresh_context_index(str(workspace))

                second.unlink()
                removed = refresh_context_index(str(workspace))

        self.assertEqual(initial["files_updated"], 2)
        self.assertEqual(initial["files_unchanged"], 0)
        self.assertEqual(unchanged["files_updated"], 0)
        self.assertEqual(unchanged["files_unchanged"], 2)
        self.assertEqual(persisted["files_updated"], 0)
        self.assertEqual(persisted["files_unchanged"], 2)
        self.assertEqual(changed["files_updated"], 1)
        self.assertEqual(changed["files_unchanged"], 1)
        self.assertEqual(removed["files_removed"], 1)
        self.assertEqual(removed["files_indexed"], 1)

    def test_refresh_removes_stale_entry_when_file_is_no_longer_utf8(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            config_dir = root / "config"
            workspace.mkdir()
            source = workspace / "source.py"
            source.write_text("SAFE = 1\n", encoding="utf-8")

            with patch.dict(
                os.environ,
                {"PROTOAGENT_CONFIG_DIR": str(config_dir)},
                clear=False,
            ):
                initial = refresh_context_index(str(workspace))
                source.write_bytes(b"\xff\xfeMALICIOUS")
                refreshed = refresh_context_index(str(workspace))
                stored = ContextStore(str(workspace)).read_file("source.py")

        self.assertEqual(initial["files_indexed"], 1)
        self.assertEqual(refreshed["files_skipped"], 1)
        self.assertEqual(refreshed["files_removed"], 1)
        self.assertEqual(refreshed["files_indexed"], 0)
        self.assertIsNone(stored)


if __name__ == "__main__":
    unittest.main()
