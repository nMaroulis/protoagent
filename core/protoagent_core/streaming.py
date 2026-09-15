"""Project native text events into provisional CLI output, separate from completion."""

from __future__ import annotations

import json


def _string_prefix(value: str) -> str:
    """Decode a JSON string prefix, withholding unfinished escapes and surrogates."""
    end = 1
    while end < len(value):
        char = value[end]
        if char == '"':
            break
        if char == "\\":
            size = 6 if value[end : end + 2] == "\\u" else 2
            if end + size > len(value):
                break
            end += size
        else:
            end += 1
    decoded = json.loads(value[:end] + '"')
    # json.loads accepts lone surrogates; terminals and UTF-8 JSONL do not.
    return decoded.encode("utf-16", "surrogatepass").decode("utf-16", "ignore")


def _final_content_prefix(raw: str) -> str:
    """Extract display text from a canonical final envelope, never an executable action.

    Wait for the top-level type before exposing content. Unsupported/unfinished
    envelopes stay hidden until ProtoLink emits its authoritative llm_final.
    Native-tool streams bypass this projection, including genuine JSON answers.
    """
    raw = raw.lstrip()
    if raw.startswith("```"):
        raw = raw.partition("\n")[2].lstrip()
    if not raw.startswith("{"):
        return ""
    decoder = json.JSONDecoder()
    remaining = raw[1:].lstrip()
    kind, content = None, ""
    try:
        while remaining and not remaining.startswith("}"):
            key, end = decoder.raw_decode(remaining)
            remaining = remaining[end:].lstrip()
            if not remaining.startswith(":"):
                break
            remaining = remaining[1:].lstrip()
            if key == "content" and remaining.startswith('"'):
                content = _string_prefix(remaining)
            value, end = decoder.raw_decode(remaining)
            if key == "type":
                kind = value
            remaining = remaining[end:].lstrip()
            if not remaining.startswith(","):
                break
            remaining = remaining[1:].lstrip()
    except (ValueError, UnicodeError):
        pass
    return content if kind == "final" else ""


class LiveOutput:
    """Project answer deltas and protect secrets split across transport chunks."""

    def __init__(self, bridge, redaction, *, json_agents=(), answer_agent="architect"):
        self.bridge = bridge
        self.redaction = redaction
        self.json_agents = frozenset(json_agents)
        self.answer_agent = answer_agent
        self.envelopes: dict[str, str] = {}
        self.projected: dict[str, str] = {}
        self.pending: dict[str, str] = {}

    def emit(self, event):
        """Display native events; only ProtoLink validates and dispatches actions."""
        kind = event.payload.get("llm_event_type")
        if kind not in {"llm_chunk", "llm_final"} and event.type != "process.output":
            return
        process = event.type == "process.output"
        channel = (
            str(event.payload.get("channel", "output"))
            if process
            else "answer"
            if event.agent_name == self.answer_agent
            else "generation"
        )
        text = event.payload.get("text" if process else "content", "")
        if not isinstance(text, str):
            return
        key = json.dumps([event.run_id, event.task_id, event.agent_name, event.step, channel])
        replace = kind == "llm_final"
        if replace:
            self.pending.pop(key, None)
            self.envelopes.pop(key, None)
            self.projected.pop(key, None)
        else:
            if not process and event.agent_name in self.json_agents:
                self.envelopes[key] = self.envelopes.get(key, "") + text
                visible = _final_content_prefix(self.envelopes[key])
                previous = self.projected.get(key, "")
                if not visible.startswith(previous):
                    return
                self.projected[key] = visible
                text = visible[len(previous) :]
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
