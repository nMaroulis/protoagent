"""Shared helpers for ProtoAgent's ProtoLink agents."""

from __future__ import annotations

import os
from typing import Any

from ..config import normalize_provider
from ..llm import create_llm_from_config

DEFAULT_AGENT_URLS = {
    "architect": "http://127.0.0.1:9110",
    "explorer": "http://127.0.0.1:9120",
    "coder": "http://127.0.0.1:9130",
}


class QuietLogger:
    """Small logger shim for embedded CLI runs."""

    def debug(self, *_args, **_kwargs):
        pass

    def info(self, *_args, **_kwargs):
        pass

    def warning(self, *_args, **_kwargs):
        pass

    def error(self, *_args, **_kwargs):
        pass

    def exception(self, *_args, **_kwargs):
        pass


QUIET_LOGGER = QuietLogger()


def create_selected_llm(provider: str, model: str | None = None):
    """Create a ProtoLink LLM object from the user's active provider/model."""
    return create_llm_from_config(normalize_provider(provider), model)


def resolve_agent_url(name: str, explicit_url: str | None = None) -> str:
    """Resolve an agent URL from explicit runtime config, env, or defaults."""
    if explicit_url:
        return explicit_url
    env_name = f"{name.upper()}_AGENT_URL"
    return os.getenv(env_name, DEFAULT_AGENT_URLS[name])


def set_transport_timeout(transport, timeout: int) -> None:
    """Apply a long request timeout across ProtoLink transport implementations."""
    if hasattr(transport, "timeout"):
        transport.timeout = timeout
    elif hasattr(transport, "_timeout"):
        transport._timeout = timeout


def record_side_effect(side_effects: list[dict[str, Any]] | None, payload: dict[str, Any]) -> None:
    """Record tool-produced payloads for the Rust CLI surface."""
    if side_effects is not None:
        side_effects.append(payload)
