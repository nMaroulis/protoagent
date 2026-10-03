"""Application-owned approval and cancellation bridge for the Rust CLI."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
import time
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from io import TextIOBase
from pathlib import Path
from tempfile import TemporaryFile
from typing import Any

from protolink import ApprovalBroker, ApprovalDecision

from .runtime_policy import RunAuthorization
from .runtime_storage import output_redaction


class RuntimeBridge:
    """Exchange typed runtime controls through short-lived local JSON files."""

    def __init__(self, progress_path: str | None) -> None:
        self.progress_path = Path(progress_path) if progress_path else None
        self.broker: ApprovalBroker | None = None
        self.authorization: RunAuthorization | None = None
        self.redaction = output_redaction()
        self._write_lock = threading.Lock()
        # Rust owns stale-control cleanup before the worker starts. Preserve a
        # cancellation that may arrive while Python is still assembling context.
        self._clear_controls(include_cancel=False)

    def emit(self, message: str, *, run_event: dict[str, Any] | None = None) -> None:
        """Append a progress record, preserving the normalized event envelope."""
        record: dict[str, Any] = {"ts": time.time(), "event": message}
        if run_event is not None:
            record["run_event"] = run_event
        self._append(record)

    def emit_output(self, output: dict[str, Any]) -> None:
        """Send provisional text independently of the bounded trace-summary channel."""
        self._append({"ts": time.time(), "live_output": output})

    @contextmanager
    def capture_console(self):
        """Keep Python diagnostics off the frontend terminal during a CLI run.

        The embedded CLI runs one Python task at a time. Capture complete text
        before redaction so secrets split across writes remain protected.
        Native process tools retain their own output capture and receipts.
        """
        if self.progress_path is None:
            yield
            return
        streams = {channel: _ConsoleBuffer() for channel in ("stdout", "stderr")}
        try:
            with (
                _capture_console_handlers(streams),
                redirect_stdout(streams["stdout"]),
                redirect_stderr(streams["stderr"]),
            ):
                yield
        finally:
            for channel, stream in streams.items():
                text = stream.getvalue()
                if stream.truncated:
                    # A truncation boundary can split a configured secret.
                    tail = max(
                        (
                            length
                            for secret in self.redaction.sensitive_values
                            for length in range(1, min(len(secret), len(text) + 1))
                            if text.endswith(secret[:length])
                        ),
                        default=0,
                    )
                    if tail:
                        text = text[:-tail] + "[REDACTED]"
                    text += "\n[Console diagnostics truncated]"
                if text:
                    self.emit_output(
                        {
                            "id": f"console-{channel}",
                            "agent": "runtime",
                            "channel": channel,
                            "text": text,
                            "replace": False,
                        }
                    )
                stream.close()

    def _append(self, record: dict[str, Any]) -> None:
        if self.progress_path is None:
            return
        record = self.redaction.redact(record)
        try:
            with self._write_lock:
                fd = os.open(
                    self.progress_path,
                    os.O_APPEND | os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW,
                    0o600,
                )
                with os.fdopen(fd, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=True) + "\n")
                    handle.flush()
        except OSError:
            pass

    def bind(self, broker: ApprovalBroker, authorization: RunAuthorization, redaction=None) -> None:
        """Attach the native broker and the application's trusted authorization scope."""
        self.broker, self.authorization = broker, authorization
        if redaction is not None:
            self.redaction = redaction

    @property
    def approval_records(self):
        """Return detached broker records for this authenticated local runtime."""
        if self.broker is None or self.authorization is None:
            return ()
        return self.broker.records(self.authorization.scope)

    @property
    def approval_requests(self) -> list[dict[str, Any]]:
        return [
            {
                **self.redaction.redact(record.request.to_dict()),
                "fingerprint": record.fingerprint,
                "status": record.status,
            }
            for record in self.approval_records
        ]

    @property
    def approval_decisions(self) -> list[dict[str, Any]]:
        return [
            self.redaction.redact(record.decision.to_dict())
            for record in self.approval_records
            if record.decision is not None
        ]

    async def serve(self, handle) -> None:
        """Present pending requests one at a time; native broker owns their waits.

        Decisions carry the exact displayed fingerprint and request ID. Scope is
        never read from the decision file. Cancellation goes to RunHandle once;
        it is not converted to an approval or a task submission retry.
        """
        presented = None
        generation = 0
        while True:
            if reason := self.cancel_reason():
                await handle.cancel(reason)
                self.emit(f"Cancellation requested: {reason}")
                return
            # Isolated agents such as Guide need cancellation, but no broker.
            if self.broker is None or self.authorization is None:
                await asyncio.sleep(0.08)
                continue
            scope = self.authorization.scope
            pending = self.broker.pending(scope)
            record = pending[0] if pending else None
            if record is None:
                self._unlink(self.request_path)
                presented = None
            elif self.progress_path is None:
                self.broker.resolve(
                    ApprovalDecision(
                        False,
                        record.request.request_id,
                        reason="No interactive ProtoAgent approval bridge is available",
                        decided_by="protoagent-core",
                    ),
                    scope=scope,
                    fingerprint=record.fingerprint,
                )
            else:
                request_id = record.request.request_id
                if presented != request_id:
                    self._write_json(
                        self.request_path,
                        {
                            **self.redaction.redact(record.request.to_dict()),
                            "fingerprint": record.fingerprint,
                            "presentation_id": generation,
                        },
                    )
                    presented = request_id
                    self.emit(f"Approval required for {record.request.action.name}.")
                data = self._read_json(self.decision_path)
                if data is not None:
                    self._unlink(self.decision_path)
                    if isinstance(data.get("approved"), bool) and isinstance(
                        data.get("fingerprint"), str
                    ):
                        resolution = self.broker.resolve(
                            ApprovalDecision.from_dict(data),
                            scope=scope,
                            fingerprint=data["fingerprint"],
                        )
                        self.emit(f"Approval decision: {resolution.status}.")
                    else:
                        self.emit(
                            "Approval decision rejected: missing exact ID/fingerprint or boolean decision."
                        )
                    # A stale or malformed decision re-presents the same native
                    # request; only the UI presentation generation changes.
                    presented = None
                    generation += 1
            await asyncio.sleep(0.08)

    def cancel_reason(self) -> str | None:
        """Return the application cancellation reason when one was requested."""
        data = self._read_json(self.cancel_path)
        if not data:
            return None
        return str(data.get("reason") or "Canceled by the ProtoAgent user")

    @property
    def request_path(self) -> Path | None:
        return self._control_path("approval-request")

    @property
    def decision_path(self) -> Path | None:
        return self._control_path("approval-decision")

    @property
    def cancel_path(self) -> Path | None:
        return self._control_path("cancel")

    def cleanup(self) -> None:
        """Remove control files after a run while leaving progress to Rust."""
        self._clear_controls()

    def _control_path(self, suffix: str) -> Path | None:
        if self.progress_path is None:
            return None
        return Path(f"{self.progress_path}.{suffix}.json")

    def _clear_controls(self, *, include_cancel: bool = True) -> None:
        paths = [self.request_path, self.decision_path]
        if include_cancel:
            paths.append(self.cancel_path)
        for path in paths:
            self._unlink(path)

    @staticmethod
    def _unlink(path: Path | None) -> None:
        if path is None:
            return
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    @staticmethod
    def _read_json(path: Path | None) -> dict[str, Any] | None:
        if path is None:
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _write_json(path: Path | None, value: dict[str, Any]) -> None:
        if path is None:
            return
        temporary = Path(f"{path}.{os.getpid()}.tmp")
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=True)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def _console_handlers():
    loggers = [logging.getLogger(), *logging.root.manager.loggerDict.copy().values()]
    handlers = {
        handler
        for logger in loggers
        if isinstance(logger, logging.Logger)
        for handler in logger.handlers
        if isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, logging.FileHandler)
    }
    return handlers


@contextmanager
def _capture_console_handlers(streams):
    """Route pre-bound SDK log streams too; preserve file logging and restore on exit.

    Redirecting sys.stdout alone misses StreamHandlers created at import time.
    Also detach handlers created during the call before closing capture buffers.
    Like redirect_stdout, this scope is for the CLI's single embedded core call.
    """
    previous = {"stdout": sys.stdout, "stderr": sys.stderr}
    originals = {}
    try:
        for handler in _console_handlers():
            for channel, candidates in (
                ("stdout", (previous["stdout"], sys.__stdout__)),
                ("stderr", (previous["stderr"], sys.__stderr__)),
            ):
                if any(
                    handler.stream is candidate for candidate in candidates if candidate is not None
                ):
                    originals[handler] = handler.stream
                    handler.setStream(streams[channel])
                    break
        yield
    finally:
        for handler in _console_handlers() | originals.keys():
            if handler in originals:
                handler.setStream(originals[handler])
            else:
                for channel, stream in streams.items():
                    if handler.stream is stream:
                        handler.setStream(previous[channel])


class _ConsoleBuffer(TextIOBase):
    """Bound console capture without blocking a provider's writes."""

    LIMIT = 32_768

    def __init__(self):
        super().__init__()
        # MCP's stdio SDK passes sys.stderr as a subprocess descriptor. A
        # private temporary file captures that output too, unlike StringIO.
        self._file = TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
        self.truncated = False

    @property
    def encoding(self):
        return "utf-8"

    def fileno(self):
        return self._file.fileno()

    def write(self, text: str) -> int:
        self._file.seek(0, os.SEEK_END)
        kept = text[: max(0, self.LIMIT - self._file.tell())]
        self._file.write(kept)
        self._file.flush()
        self.truncated |= len(kept) < len(text)
        return len(text)

    def getvalue(self):
        self._file.seek(0)
        text = self._file.read(self.LIMIT + 1)
        self.truncated |= len(text) > self.LIMIT
        return text[: self.LIMIT]

    def flush(self):
        if not self._file.closed:
            self._file.flush()

    def close(self):
        super().close()
        self._file.close()
