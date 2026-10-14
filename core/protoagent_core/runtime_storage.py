"""Application credential selection and terminal sanitization for native redaction."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from protolink import DEFAULT_REDACTION_POLICY, RedactionPolicy

from . import config

_CONTROL = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))|[\x00-\x08\x0b-\x1f\x7f]"
)


@dataclass(frozen=True)
class OutputRedaction(RedactionPolicy):
    """Strip terminal controls after ProtoLink masks fields and known secret values."""

    def redact(self, value):
        data = super().redact(value)

        def clean(item):
            if isinstance(item, str):
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
    servers = config.load_config().get("mcp_servers", {})
    for server in servers.values() if isinstance(servers, dict) else ():
        if isinstance(server, dict):
            references = server.get("headers_env", {})
            for variable in references.values() if isinstance(references, dict) else ():
                if isinstance(variable, str) and (value := os.getenv(variable)):
                    values.add(value)
    return OutputRedaction(
        sensitive_values=frozenset(value for value in values if value),
    )
