"""Provider-to-protolink LLM wiring."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import AsyncIterator
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
    if requested == "lmstudio":
        return _create_openai_compatible_chat_llm(requested, model)

    from protolink.llms.factory import create_llm

    return create_llm(protolink_provider(requested), **llm_kwargs(requested, model))


def _create_openai_compatible_chat_llm(provider: str, model: str | None = None):
    from protolink.llms.base import LLM

    class OpenAICompatibleChatLLM(LLM):
        provider = "lmstudio"
        model_type = "server"

        def __init__(self, *, base_url: str, model: str, api_key: str | None = None):
            self.base_url = base_url.rstrip("/")
            self.api_key = api_key
            super().__init__(model=model, model_params={"temperature": 0.2})

        def call(self, history) -> str:
            url = f"{self.base_url}/chat/completions" if self.base_url.endswith("/v1") else f"{self.base_url}/v1/chat/completions"
            payload = {
                "model": self.model,
                "messages": history.messages,
                "stream": False,
                **self._model_params,
            }
            headers = {"Content-Type": "application/json", "Accept": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            request = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=300) as response:
                data = json.loads(response.read().decode("utf-8"))
            choices = data.get("choices", [])
            if not choices:
                return ""
            return choices[0].get("message", {}).get("content", "")

        async def call_stream(self, history) -> AsyncIterator[str]:
            yield self.call(history)

        def validate_connection(self) -> bool:
            url = f"{self.base_url}/models" if self.base_url.endswith("/v1") else f"{self.base_url}/v1/models"
            try:
                request = urllib.request.Request(url, headers={"Accept": "application/json"})
                with urllib.request.urlopen(request, timeout=2):
                    return True
            except (urllib.error.URLError, TimeoutError, OSError):
                return False

    kwargs = llm_kwargs(provider, model)
    return OpenAICompatibleChatLLM(
        base_url=kwargs.get("base_url") or "http://localhost:1234/v1",
        model=kwargs.get("model") or "local-model",
        api_key=kwargs.get("api_key"),
    )


def validate_protolink() -> dict[str, Any]:
    try:
        import protolink  # noqa: F401
        from protolink.agents import Agent  # noqa: F401
        from protolink.llms.factory import create_llm  # noqa: F401

        return {"installed": True, "agent_ready": True, "error": ""}
    except Exception as exc:  # pragma: no cover - used for diagnostics
        try:
            import protolink  # noqa: F401

            return {"installed": True, "agent_ready": False, "error": str(exc)}
        except Exception:
            return {"installed": False, "agent_ready": False, "error": str(exc)}
