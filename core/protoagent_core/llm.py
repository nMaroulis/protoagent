"""Provider-to-protolink LLM wiring."""

from __future__ import annotations

from typing import Any

from .config import normalize_provider, provider_config


def protolink_provider(provider: str) -> str:
    provider = normalize_provider(provider)
    if provider == "lmstudio":
        return "openai"
    return provider


def llm_kwargs(provider: str, model: str | None = None) -> dict[str, Any]:
    provider = normalize_provider(provider)
    cfg = provider_config(provider)
    selected_model = model or cfg.get("model")
    kwargs: dict[str, Any] = {}

    if selected_model:
        kwargs["model"] = selected_model

    api_key = cfg.get("api_key")
    if api_key:
        kwargs["api_key"] = api_key

    base_url = cfg.get("base_url")
    if provider in {"ollama", "lmstudio", "llama.cpp-server", "deepseek"} and base_url:
        kwargs["base_url"] = base_url

    if provider == "lmstudio" and "api_key" not in kwargs:
        kwargs["api_key"] = "lm-studio"

    return kwargs


def create_llm_from_config(provider: str | None = None, model: str | None = None):
    """Create a protolink LLM instance for the selected provider.

    Imports are intentionally lazy so the CLI can still run model discovery
    and setup flows before protolink or optional provider SDKs are installed.
    """
    cfg = provider_config(provider)
    requested = normalize_provider(provider or cfg["id"])
    from protolink.llms.factory import create_llm

    return create_llm(protolink_provider(requested), **llm_kwargs(requested, model))


def validate_protolink() -> dict[str, Any]:
    try:
        import protolink  # noqa: F401

        return {"installed": True, "error": ""}
    except Exception as exc:  # pragma: no cover - used for diagnostics
        return {"installed": False, "error": str(exc)}
