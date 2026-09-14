"""Project native text events into provisional CLI output, separate from completion."""

from __future__ import annotations

import json


class LiveOutput:
    """Forward deltas while withholding known-secret prefixes split across chunks."""

    def __init__(self, bridge, redaction):
        self.bridge = bridge
        self.redaction = redaction
        self.pending: dict[str, str] = {}

    def emit(self, event):
        """Consume native llm_chunk/llm_final or process.output without parsing actions."""
        kind = event.payload.get("llm_event_type")
        if kind not in {"llm_chunk", "llm_final"} and event.type != "process.output":
            return
        process = event.type == "process.output"
        channel = str(event.payload.get("channel", "output")) if process else "generation"
        text = event.payload.get("text" if process else "content", "")
        if not isinstance(text, str):
            return
        key = json.dumps([event.run_id, event.task_id, event.agent_name, event.step, channel])
        replace = kind == "llm_final"
        if replace:
            self.pending.pop(key, None)
        else:
            text = self.pending.pop(key, "") + text
            text = self.redaction.redact(text)
            # A full secret is handled by the native policy. A proper prefix at
            # the end must wait for the next delta rather than leak to the UI.
            hold = max(
                (
                    length
                    for secret in self.redaction.sensitive_values
                    for length in range(1, min(len(secret), len(text) + 1))
                    if text.endswith(secret[:length])
                ),
                default=0,
            )
            if hold:
                self.pending[key], text = text[-hold:], text[:-hold]
        if text or replace:
            self.bridge.emit_output(
                {
                    "id": key,
                    "agent": event.agent_name or "runtime",
                    "channel": channel,
                    "text": self.redaction.redact(text),
                    "replace": replace,
                }
            )
