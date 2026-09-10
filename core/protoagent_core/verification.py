"""Bounded process execution and factual verification evidence for ProtoLink tools.

This module supplies application behavior, not agent orchestration or policy.
Only the Verifier's authorized ProtoLink tool calls may launch these processes.
A working directory is not a sandbox: approved commands inherit host access.
"""

from __future__ import annotations

import asyncio
import codecs
import os
import re
import shlex
import signal
import threading
import time
from collections.abc import Callable
from typing import Any

from .tools import safe_path

MAX_OUTPUT_BYTES = 32_768
MAX_TIMEOUT_SECONDS = 600


class VerificationEvidence:
    """Track command outcomes against the current revision of agent file changes."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._revision = 0
        self._results: list[dict[str, Any]] = []

    def changed(self) -> None:
        """Invalidate earlier verification after an authorized Coder mutation."""
        with self._lock:
            self._revision += 1

    def revision(self) -> int:
        """Return the current change generation before a command starts."""
        with self._lock:
            return self._revision

    def record(self, result: dict[str, Any], revision: int) -> None:
        """Retain an actual tool result without interpreting model prose."""
        with self._lock:
            self._results.append({**result, "revision": revision})

    def report(self) -> dict[str, Any]:
        """Summarize the latest result per command for the current generation."""
        with self._lock:
            results = [dict(result) for result in self._results]
            revision = self._revision
        latest: dict[tuple[str, str], dict[str, Any]] = {}
        for result in results:
            if result["revision"] == revision:
                latest[(result["cwd"], result["command"])] = result
        status = "not-run"
        if latest:
            status = "passed" if all(item["success"] for item in latest.values()) else "failed"
        return {"status": status, "revision": revision, "results": results}


def validate_command(
    argv: list[str], cwd: str, timeout_seconds: int, workspace: str | None
) -> tuple[list[str], str, int]:
    """Validate the exact argv, project directory, and bounded command timeout."""
    if (
        not argv
        or len(argv) > 64
        or any(
            not isinstance(arg, str) or any(ord(ch) < 32 or ord(ch) == 127 for ch in arg)
            for arg in argv
        )
    ):
        raise ValueError("Command must contain 1–64 arguments without control characters")
    if not argv[0].strip() or sum(len(arg) for arg in argv) > 8192:
        raise ValueError("Command executable is empty or arguments exceed 8192 characters")
    if not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise ValueError(f"Command timeout must be between 1 and {MAX_TIMEOUT_SECONDS} seconds")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in cwd):
        raise ValueError("Command directory must not contain control characters")
    directory = safe_path(cwd, workspace)
    if not directory.is_dir():
        raise ValueError(f"Command directory does not exist: {cwd}")
    return list(argv), str(directory), timeout_seconds


def terminal_text(value: str) -> str:
    """Remove terminal escape sequences and control bytes from process output."""
    value = re.sub(r"\x1b\][^\x07]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]", "", value)
    return "".join(ch for ch in value if ch in "\n\t" or (ch.isprintable() and ch != "\x7f"))


async def run_command(
    argv: list[str],
    cwd: str = ".",
    timeout_seconds: int = 120,
    *,
    workspace: str | None = None,
    on_output: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run approved argv without a shell, bounding output and process lifetime.

    Standard input is closed. POSIX descendants share a fresh process group,
    killed on timeout or asyncio cancellation from ProtoLink. On other systems
    the direct child is killed. Output beyond the budget is drained and dropped.
    """
    argv, directory, timeout_seconds = validate_command(argv, cwd, timeout_seconds, workspace)
    started = time.monotonic()
    output = bytearray()
    truncated = False
    timed_out = False
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=directory,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=os.name == "posix",
        )
    except OSError as exc:
        return {
            "success": False,
            "command": shlex.join(argv),
            "cwd": directory,
            "exit_code": None,
            "output": str(exc),
            "truncated": False,
            "timed_out": False,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }

    async def drain() -> None:
        nonlocal truncated
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        assert process is not None and process.stdout is not None
        while chunk := await process.stdout.read(4096):
            remaining = MAX_OUTPUT_BYTES - len(output)
            retained = chunk[:remaining]
            output.extend(retained)
            truncated |= len(retained) < len(chunk)
            text = terminal_text(decoder.decode(retained))
            if on_output and text:
                on_output(text)
        tail = terminal_text(decoder.decode(b"", final=True))
        if on_output and tail:
            on_output(tail)

    async def stop() -> None:
        assert process is not None
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            elif process.returncode is None:
                process.kill()
        except ProcessLookupError:
            pass
        await process.wait()

    try:
        await asyncio.wait_for(asyncio.gather(drain(), process.wait()), timeout_seconds)
    except TimeoutError:
        timed_out = True
        await stop()
    except asyncio.CancelledError:
        await stop()
        raise
    except BaseException:
        await stop()
        raise
    return {
        "success": process.returncode == 0 and not timed_out,
        "command": shlex.join(argv),
        "cwd": directory,
        "exit_code": process.returncode,
        "output": terminal_text(output.decode("utf-8", errors="replace")),
        "truncated": truncated,
        "timed_out": timed_out,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }


def verification_summary(report: dict[str, Any]) -> str:
    """Render measured command outcomes, including checks invalidated by changes."""
    lines = [f"Verification: {report['status']}."]
    if not report["results"]:
        return lines[0] + " No test/build command was executed."
    for item in report["results"]:
        outcome = "timeout" if item["timed_out"] else f"exit {item['exit_code']}"
        stale = (
            " (before the latest agent change)" if item["revision"] != report["revision"] else ""
        )
        lines.append(f"- {item['command']}: {outcome}{stale}")
    return "\n".join(lines)
