"""Disposable coding exercises with independent acceptance tests and a same-model baseline."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, TypedDict
from unittest.mock import patch

from protolink import ApprovalDecision

from . import config
from ._version import __version__
from .prompt_profiles import prompt_profile_status
from .runtime_bridge import RuntimeBridge


class Exercise(TypedDict):
    id: str
    prompt: str
    files: dict[str, str]
    public: str
    oracle: str


EXERCISES: tuple[Exercise, ...] = (
    {
        "id": "empty-average",
        "prompt": "Fix average in sample.py: empty input must return 0, and nonempty averages must remain correct. Add a regression test and run the tests.",
        "files": {"sample.py": "def average(values):\n    return sum(values) / len(values)\n"},
        "public": "from sample import average\nassert average([2, 4]) == 3\n",
        "oracle": "from sample import average\nassert average([]) == 0\nassert average([1, 2, 3]) == 2\nassert average([-2, 2]) == 0\n",
    },
    {
        "id": "whitespace-slug",
        "prompt": "Fix slug in sample.py: lowercase text, trim surrounding whitespace, and collapse any run of whitespace to one hyphen. Empty input returns an empty string. Add regression coverage and run the tests.",
        "files": {
            "sample.py": "def slug(text):\n    return text.strip().lower().replace(' ', '-')\n"
        },
        "public": "from sample import slug\nassert slug('Hello World') == 'hello-world'\n",
        "oracle": "from sample import slug\nassert slug(' A  B\\tC\\nD ') == 'a-b-c-d'\nassert slug('') == ''\nassert slug('  ') == ''\nassert slug('Hello World') == 'hello-world'\n",
    },
    {
        "id": "cross-file-flags",
        "prompt": "Fix enabled in sample.py and the is_enabled caller in app.py so they accept case-insensitive true/false strings with surrounding whitespace, preserve bool inputs, and raise ValueError for other strings. Add regression coverage and run the tests.",
        "files": {
            "sample.py": "def enabled(value):\n    return bool(value)\n",
            "app.py": "from sample import enabled\n\ndef is_enabled(value):\n    return enabled(str(value))\n",
        },
        "public": "from app import is_enabled\nassert is_enabled(True) is True\n",
        "oracle": "from app import is_enabled\nassert is_enabled(False) is False\nassert is_enabled(' TRUE ') is True\nassert is_enabled('false') is False\ntry:\n    is_enabled('maybe')\nexcept ValueError:\n    pass\nelse:\n    raise AssertionError('invalid string accepted')\n",
    },
)


class FixtureBridge(RuntimeBridge):
    """Approve only fixture writes and the exact preconfigured fixture test command."""

    def __init__(self, root: Path, allowed: set[str], argv: list[str]):
        super().__init__(None)
        self.root, self.allowed, self.argv = root, allowed, argv

    async def serve(self, handle):
        while True:
            if self.broker is not None and self.authorization is not None:
                for pending in self.broker.pending(self.authorization.scope):
                    action = pending.request.action
                    args = action.payload.get("arguments", {})
                    approved = (
                        action.name in {"create_file", "replace_file"}
                        and args.get("path") in self.allowed
                    ) or (
                        action.name == "execute_command"
                        and args.get("argv") == self.argv
                        and args.get("cwd") == str(self.root)
                        and args.get("env") == {"PYTHONPATH": str(self.root)}
                    )
                    self.broker.resolve(
                        ApprovalDecision(approved, pending.request.request_id),
                        scope=self.authorization.scope,
                        fingerprint=pending.fingerprint,
                    )
            await asyncio.sleep(0.01)


def oracle_result(root: Path, oracle: str) -> dict:
    """Use a separate script and bound returned output; clean up its process group."""
    with tempfile.TemporaryDirectory() as directory:
        script = Path(directory) / "acceptance.py"
        script.write_text(oracle)
        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen(
                [sys.executable, str(script)],
                cwd=root,
                env={"PYTHONPATH": str(root)},
                stdout=output,
                stderr=output,
                start_new_session=True,
            )
            try:
                process.wait(timeout=10)
                output.seek(0)
                return {
                    "passed": process.returncode == 0,
                    "exit_code": process.returncode,
                    "output": output.read(4096).decode("utf-8", "replace"),
                }
            except subprocess.TimeoutExpired:
                return {"passed": False, "exit_code": None, "output": "Acceptance timed out"}
            finally:
                if os.name == "posix":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                elif process.poll() is None:
                    process.kill()
                process.wait()


def token_usage(metrics: list[dict]) -> dict:
    """Keep unavailable usage null and mark provider/estimated mixed accounting."""
    result = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        values = [metric.get("usage", {}).get(key) for metric in metrics]
        result[key] = (
            sum(value for value in values if value is not None)
            if any(value is not None for value in values)
            else None
        )
    result["estimated"] = (
        any(metric.get("usage", {}).get("estimated", True) for metric in metrics)
        if metrics
        else None
    )
    result["call_count"] = len(metrics)
    return result


def run_coding_eval(*, profiles=None, task_ids=None, mode="plan", limit=None) -> dict:
    """Run only with --live; plans and scaffold never contact a model or approve effects."""
    from .quality_eval import _normalize_profiles
    from .runtime import _run_agent_deck

    selected_profiles = _normalize_profiles(profiles or "small")
    ids = (
        [x.strip() for x in task_ids.split(",") if x.strip()]
        if isinstance(task_ids, str)
        else list(task_ids or [])
    )
    tasks = [item for item in EXERCISES if not ids or item["id"] in ids]
    if ids and set(ids) - {item["id"] for item in EXERCISES}:
        raise ValueError("Unknown coding exercise ID")
    if limit is not None:
        tasks = tasks[: max(0, limit)]
    if mode not in {"plan", "scaffold", "live"}:
        raise ValueError("Use plan, scaffold or live")
    source_config = config.load_config()
    provider = source_config["active_provider"]
    model = source_config["providers"][provider].get("model", "")
    reports: list[dict[str, Any]] = []
    started = time.monotonic()
    for profile in selected_profiles:
        results: list[dict[str, Any]] = []
        for exercise in tasks:
            for architecture in ("deck", "single"):
                if mode != "live":
                    results.append(
                        {
                            "task_id": exercise["id"],
                            "architecture": architecture,
                            "score": None,
                            "error": "",
                        }
                    )
                    continue
                if not model:
                    raise ValueError("Select a model before running live coding evals")
                with tempfile.TemporaryDirectory(prefix="protoagent-eval-") as directory:
                    root = (Path(directory) / "workspace").resolve()
                    root.mkdir()
                    for name, content in exercise["files"].items():
                        (root / name).write_text(content)
                    (root / "tests").mkdir()
                    (root / "tests" / "test_sample.py").write_text(
                        "import unittest\n\nclass Regression(unittest.TestCase):\n    def test_existing(self):\n"
                        + "".join(
                            "        " + line + "\n" for line in exercise["public"].splitlines()
                        )
                    )
                    argv = [
                        str(Path(sys.executable).absolute()),
                        "-m",
                        "unittest",
                        "discover",
                        "-s",
                        "tests",
                        "-q",
                    ]
                    (root / ".protoagent").mkdir()
                    (root / ".protoagent" / "project.json").write_text(
                        json.dumps(
                            {
                                "checks": [
                                    {
                                        "id": "regression",
                                        "argv": argv,
                                        "env": {"PYTHONPATH": str(root)},
                                        "paths": ["tests"],
                                    }
                                ]
                            }
                        )
                    )
                    before = oracle_result(root, exercise["oracle"])
                    cfg = dict(
                        source_config,
                        agent_prompt_profile=profile,
                        optional_agents={
                            "scout": {"enabled": False},
                            "mcp": {"enabled": False},
                            "tester": {"enabled": True},
                        },
                    )
                    private = Path(directory) / "state"
                    bridge = FixtureBridge(
                        root,
                        {str(root / name) for name in [*exercise["files"], "tests/test_sample.py"]},
                        argv,
                    )
                    tick = time.monotonic()
                    error = ""
                    response = {}
                    with (
                        patch.object(config, "CONFIG_DIR", private),
                        patch.object(config, "CONFIG_PATH", private / "config.json"),
                        patch.dict(os.environ, {"PROTOAGENT_SCAFFOLD": "0"}),
                    ):
                        config.save_config(cfg)
                        try:
                            response = asyncio.run(
                                _run_agent_deck(
                                    exercise["prompt"],
                                    provider,
                                    model,
                                    str(root),
                                    None,
                                    bridge,
                                    prompt_profile_status(cfg),
                                    architecture=architecture,
                                )
                            )
                        except Exception as exc:
                            error = str(exc)
                        finally:
                            bridge.cleanup()
                    after = oracle_result(root, exercise["oracle"])
                    success = (
                        not before["passed"]
                        and after["passed"]
                        and response.get("status") == "completed"
                    )
                    events = response.get("run_events", [])
                    metrics = [
                        event.get("payload", {}).get("metadata", {})
                        for event in events
                        if event.get("payload", {}).get("llm_event_type") == "llm_call_metrics"
                    ]
                    results.append(
                        {
                            "task_id": exercise["id"],
                            "architecture": architecture,
                            "score": {"score": float(success)},
                            "error": error,
                            "status": response.get("status"),
                            "oracle_before": before,
                            "oracle_after": after,
                            "false_completion": response.get("status") == "completed"
                            and not after["passed"],
                            "elapsed_ms": int((time.monotonic() - tick) * 1000),
                            "attempts": response.get("verification", {}).get("attempts", 0),
                            "repair_attempts": max(
                                0, response.get("verification", {}).get("attempts", 0) - 1
                            ),
                            "llm_call_metrics": metrics,
                            "token_usage": token_usage(metrics),
                            "context_admission": response.get("context_admission", {}),
                        }
                    )
        scores = [r["score"]["score"] for r in results if r["score"] is not None]
        reports.append(
            {
                "profile": profile,
                "tasks": results,
                "points": sum(int(score) for score in scores),
                "possible": len(scores),
                "score": sum(scores) / len(scores) if scores else None,
                "architectures": {
                    architecture: {
                        "points": sum(
                            int(item["score"]["score"])
                            for item in results
                            if item["architecture"] == architecture and item["score"] is not None
                        ),
                        "possible": sum(
                            item["architecture"] == architecture and item["score"] is not None
                            for item in results
                        ),
                        "false_completions": sum(
                            item.get("false_completion", False)
                            for item in results
                            if item["architecture"] == architecture
                        ),
                    }
                    for architecture in ("deck", "single")
                },
            }
        )
    points = sum(report["points"] for report in reports)
    possible = sum(report["possible"] for report in reports)
    return {
        "version": 1,
        "core_version": __version__,
        "provider": provider,
        "model": model,
        "mode": mode,
        "workspace": "disposable fixtures",
        "profiles": reports,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "summary": {
            "profile_count": len(reports),
            "task_count": len(tasks),
            "run_count": len(reports) * len(tasks) * 2,
            "points": points,
            "possible": possible,
            "score": points / possible if possible else None,
        },
        "notes": [
            "Deck and single-agent baseline use the same selected model and approval rules.",
            "Independent acceptance scripts remain outside the model workspace.",
            "Disposable workspaces are not sandboxes; approved tests execute on the host.",
            "Model success claims are checked against the independent oracle.",
        ],
    }
