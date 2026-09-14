"""Application-owned approval and cancellation bridge for the Rust CLI."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from pathlib import Path
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
        assert self.broker is not None and self.authorization is not None
        presented = None
        generation = 0
        while True:
            if reason := self.cancel_reason():
                await handle.cancel(reason)
                self.emit(f"Cancellation requested: {reason}")
                return
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
