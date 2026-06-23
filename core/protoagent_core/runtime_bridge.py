"""Application-owned approval and cancellation bridge for the Rust CLI."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from protolink import ApprovalDecision, ApprovalRequest, RunContext


class RuntimeBridge:
    """Exchange typed runtime controls through short-lived local JSON files."""

    def __init__(self, progress_path: str | None) -> None:
        self.progress_path = Path(progress_path) if progress_path else None
        self.approval_requests: list[dict[str, Any]] = []
        self.approval_decisions: list[dict[str, Any]] = []
        self._write_lock = threading.Lock()
        # Rust owns stale-control cleanup before the worker starts. Preserve a
        # cancellation that may arrive while Python is still assembling context.
        self._clear_controls(include_cancel=False)

    def emit(self, message: str, *, run_event: dict[str, Any] | None = None) -> None:
        """Append a progress record, preserving the normalized event envelope."""
        if self.progress_path is None:
            return
        record: dict[str, Any] = {"ts": time.time(), "event": message}
        if run_event is not None:
            record["run_event"] = run_event
        try:
            with self._write_lock, self.progress_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=True) + "\n")
                handle.flush()
        except OSError:
            pass

    async def approval_handler(
        self,
        request: ApprovalRequest,
        _context: RunContext,
    ) -> ApprovalDecision:
        """Wait for the Rust application to answer a typed approval request."""
        request_data = request.to_dict()
        self.approval_requests.append(request_data)
        if self.progress_path is None:
            decision = ApprovalDecision(
                approved=False,
                request_id=request.request_id,
                reason="No interactive ProtoAgent approval bridge is available",
                decided_by="protoagent-core",
            )
            self.approval_decisions.append(decision.to_dict())
            return decision

        self._unlink(self.decision_path)
        self._write_json(self.request_path, request_data)
        self.emit(f"Approval required for {request.action.description or request.action.name}.")

        while True:
            cancel_reason = self.cancel_reason()
            if cancel_reason:
                decision = ApprovalDecision(
                    approved=False,
                    request_id=request.request_id,
                    reason=cancel_reason,
                    decided_by="protoagent-user",
                )
                break

            data = self._read_json(self.decision_path)
            if data and data.get("request_id") == request.request_id:
                decision = ApprovalDecision.from_dict(data)
                break
            await asyncio.sleep(0.08)

        self.approval_decisions.append(decision.to_dict())
        self._unlink(self.request_path)
        self._unlink(self.decision_path)
        return decision

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
        temporary.write_text(json.dumps(value, ensure_ascii=True), encoding="utf-8")
        os.replace(temporary, path)
