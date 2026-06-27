"""Provider-to-ProtoLink LLM wiring."""

from __future__ import annotations

import os
from typing import Any

from .config import normalize_provider, provider_config

DEFAULT_OLLAMA_CONTEXT_WINDOW = 8_192


def protolink_provider(provider: str) -> str:
    """Map ProtoAgent provider IDs to ProtoLink provider IDs."""
    return normalize_provider(provider)


def llm_kwargs(provider: str, model: str | None = None) -> dict[str, Any]:
    """Build provider-request arguments for ProtoLink LLM construction.

    Observability metadata is configured separately through
    :meth:`protolink.llms.base.LLM.configure_metrics` so it cannot drift into a
    provider request payload.
    """
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
    if (
        provider in {"ollama", "lmstudio", "llama.cpp-server", "deepseek", "openai-compatible"}
        and base_url
    ):
        kwargs["base_url"] = base_url

    if provider == "lmstudio" and "api_key" not in kwargs:
        kwargs["api_key"] = "lm-studio"

    if provider == "ollama":
        context_window = ollama_context_window(cfg)
        model_params = dict(cfg.get("model_params") or {})
        model_params["num_ctx"] = context_window
        kwargs["model_params"] = model_params

    return kwargs


def llm_model_profile(provider: str, model: str | None = None):
    """Build ProtoLink's typed metrics profile for the selected model."""
    from protolink import LLMModelProfile

    provider = normalize_provider(provider)
    cfg = provider_config(provider)
    selected_model = model or cfg.get("model") or None
    context_window = (
        ollama_context_window(cfg)
        if provider == "ollama"
        else _optional_positive_int(cfg.get("context_window"))
    )
    return LLMModelProfile(
        context_window=context_window,
        input_cost_per_million=_optional_nonnegative_float(cfg.get("input_cost_per_million")),
        output_cost_per_million=_optional_nonnegative_float(cfg.get("output_cost_per_million")),
        currency=str(cfg.get("currency") or "USD"),
        provider=provider,
        model=str(selected_model) if selected_model else None,
        supports_tools=True,
        supports_streaming=True,
        supports_json_schema=True,
        tokenizer=_optional_str(cfg.get("tokenizer")),
        metadata={"configured_by": "protoagent"},
    )


def ollama_context_window(config: dict[str, Any] | None = None) -> int:
    """Return the per-request Ollama context window used by ProtoAgent."""
    return int(ollama_context_window_details(config)["window_tokens"])


def ollama_context_window_details(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the effective Ollama context window and where it came from."""
    cfg = provider_config("ollama") if config is None else config
    raw_model_params = cfg.get("model_params")
    model_params: dict[str, Any] = raw_model_params if isinstance(raw_model_params, dict) else {}
    candidates = (
        (cfg.get("context_window"), "app config"),
        (os.getenv("PROTOAGENT_OLLAMA_NUM_CTX"), "PROTOAGENT_OLLAMA_NUM_CTX"),
        (model_params.get("num_ctx"), "provider model_params"),
        (os.getenv("OLLAMA_CONTEXT_LENGTH"), "OLLAMA_CONTEXT_LENGTH"),
        (DEFAULT_OLLAMA_CONTEXT_WINDOW, "ProtoAgent default"),
    )
    for value, source in candidates:
        if value is None:
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return {
                "window_tokens": parsed,
                "configured_tokens": cfg.get("context_window"),
                "source": source,
            }
    return {
        "window_tokens": DEFAULT_OLLAMA_CONTEXT_WINDOW,
        "configured_tokens": None,
        "source": "ProtoAgent default",
    }


def create_llm_from_config(provider: str | None = None, model: str | None = None):
    """Create a protolink LLM instance for the selected provider.

    Imports are intentionally lazy so the CLI can still run model discovery
    and setup flows before protolink or optional provider SDKs are installed.
    """
    cfg = provider_config(provider)
    requested = normalize_provider(provider or cfg["id"])
    from protolink.llms.factory import create_llm

    llm = create_llm(protolink_provider(requested), **llm_kwargs(requested, model))
    llm.configure_metrics(llm_model_profile(requested, model))
    return llm


def validate_protolink() -> dict[str, Any]:
    """Report whether ProtoLink and its agent runtime can be imported."""
    try:
        import protolink
        from protolink import (
            ContextManifest,
            HistoryCompactor,
            LLMModelProfile,
            RedactionPolicy,
            RunRecorder,
            StateOperationResult,
            TaskCancellationRequest,
        )
        from protolink.agents import Agent
        from protolink.client import AgentClient
        from protolink.llms.base import LLM
        from protolink.llms.factory import create_llm  # noqa: F401

        streaming_ready = hasattr(Agent, "handle_task_streaming") and hasattr(
            AgentClient, "send_task_streaming"
        )
        metrics_ready = hasattr(LLM, "configure_metrics") and LLMModelProfile is not None
        compaction_ready = hasattr(LLM, "compact_history") and HistoryCompactor is not None
        context_manifest_ready = ContextManifest is not None
        run_report_ready = RunRecorder is not None and RedactionPolicy is not None
        state_ready = (
            StateOperationResult is not None
            and hasattr(Agent, "describe_state")
            and hasattr(AgentClient, "describe_state")
            and hasattr(Agent, "reset_state")
            and hasattr(AgentClient, "reset_state")
            and hasattr(Agent, "compact_state")
            and hasattr(AgentClient, "compact_state")
        )
        cancellation_ready = (
            hasattr(Agent, "cancel_task")
            and hasattr(AgentClient, "cancel_task")
            and TaskCancellationRequest is not None
        )
        agent_ready = all(
            (
                streaming_ready,
                metrics_ready,
                compaction_ready,
                context_manifest_ready,
                run_report_ready,
                state_ready,
                cancellation_ready,
            )
        )

        return {
            "installed": True,
            "version": getattr(protolink, "__version__", ""),
            "agent_ready": agent_ready,
            "streaming_ready": streaming_ready,
            "metrics_ready": metrics_ready,
            "compaction_ready": compaction_ready,
            "context_manifest_ready": context_manifest_ready,
            "run_report_ready": run_report_ready,
            "state_ready": state_ready,
            "cancellation_ready": cancellation_ready,
            "error": "",
        }
    except Exception as exc:  # pragma: no cover - used for diagnostics
        try:
            import protolink

            return {
                "installed": True,
                "version": getattr(protolink, "__version__", ""),
                "agent_ready": False,
                "streaming_ready": False,
                "metrics_ready": False,
                "compaction_ready": False,
                "context_manifest_ready": False,
                "run_report_ready": False,
                "state_ready": False,
                "cancellation_ready": False,
                "error": str(exc),
            }
        except Exception:
            return {
                "installed": False,
                "version": "",
                "agent_ready": False,
                "streaming_ready": False,
                "metrics_ready": False,
                "compaction_ready": False,
                "context_manifest_ready": False,
                "run_report_ready": False,
                "state_ready": False,
                "cancellation_ready": False,
                "error": str(exc),
            }


def _optional_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _optional_nonnegative_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
