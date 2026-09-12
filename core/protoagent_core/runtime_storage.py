"""Application storage locations, output redaction and native trace composition."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from protolink import DEFAULT_REDACTION_POLICY, RedactionPolicy, RunEvent, RunReport, Task
from protolink.storage import SQLiteRunStore

from . import config
from .checkpoints import private_file

_CONTROL = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))|[\x00-\x08\x0b-\x1f\x7f]"
)


@dataclass(frozen=True)
class OutputRedaction(RedactionPolicy):
    """Mask configured credential values even in command output and model prose."""

    secret_values: tuple[str, ...] = ()

    def redact(self, value):
        data = super().redact(value)

        def clean(item):
            if isinstance(item, str):
                for secret in self.secret_values:
                    item = item.replace(secret, self.replacement)
                return _CONTROL.sub("", item)
            if isinstance(item, dict):
                return {key: clean(entry) for key, entry in item.items()}
            if isinstance(item, (list, tuple)):
                return [clean(entry) for entry in item]
            return item

        return clean(data)


def output_redaction(*secrets: str) -> OutputRedaction:
    """Protect recovery bytes and known keys; arbitrary printed secrets may remain."""
    values = {
        value for key, value in os.environ.items() if DEFAULT_REDACTION_POLICY.is_sensitive_key(key)
    }
    for provider in config.load_config().get("providers", {}).values():
        if isinstance(provider, dict):
            values.update(
                str(value)
                for key, value in provider.items()
                if DEFAULT_REDACTION_POLICY.is_sensitive_key(key) and value
            )
    values.update(secret for secret in secrets if secret)
    return OutputRedaction(
        sensitive_keys=DEFAULT_REDACTION_POLICY.sensitive_keys | {"data_base64"},
        secret_values=tuple(sorted((value for value in values if value), key=len, reverse=True)),
    )


class ApplicationRunStore(SQLiteRunStore):
    """Use native persistence with mandatory redaction for automatic agent snapshots."""

    def __init__(self, path: Path, redaction: RedactionPolicy):
        self.redaction = redaction
        super().__init__(private_file(path))

    def save_task(self, task, **kwargs):
        return super().save_task(Task.from_dict(self.redaction.redact(task.to_dict())), **kwargs)

    def save_report(self, report, **kwargs):
        return super().save_report(
            RunReport.from_dict(self.redaction.redact(report.to_dict())), **kwargs
        )

    def trace_report(self, task: Task, *, observed: Iterable[RunEvent] = ()) -> RunReport:
        """Join native worker receipts from this run's dedicated store by event ID.

        ProtoLink 0.7.0 delegation returns outputs without merging worker events.
        Native task snapshots provide execution evidence without interpreting LLM
        tool-result strings or reconstructing terminal transport events.
        """
        from protolink import RunContext

        context = RunContext.from_task(task)
        events: dict[str, RunEvent] = {event.event_id: event for event in observed}
        # This database belongs to one run; a generous finite inspection bound
        # is safe with the application's native execution budgets.
        records = self.list_task_records(limit=10001)
        if len(records) > 10000:
            raise ValueError(
                "Native snapshot inspection limit reached; completion cannot be certified"
            )
        for record in reversed(records):
            if record.trace_id != context.trace_id:
                continue
            child = RunReport.from_task(Task.from_dict(record.task))
            events.update((event.event_id, event) for event in child.events)
        events.update((event.event_id, event) for event in RunReport.from_task(task).events)
        ordered_events = sorted(events.values(), key=lambda event: event.timestamp)
        return RunReport.from_events(
            ordered_events,
            context=context,
            final_task=task.to_dict(),
        )
