"""Provider-to-ProtoLink LLM wiring."""

from __future__ import annotations

import inspect
import os
import time
from typing import Any

from .config import normalize_provider, provider_config

DEFAULT_OLLAMA_CONTEXT_WINDOW = 8_192
_OLLAMA_TOOL_CACHE: dict[tuple[str, str], tuple[float, bool]] = {}


def ollama_native_tools(config: dict[str, Any], model: str | None) -> bool:
    """Select native tools from model metadata, with a bounded cached probe.

    This is capability discovery, not inference or action dispatch. Missing
    metadata keeps ProtoLink's JSON fallback; explicit overrides need no probe.
    """
    mode = config.get("tool_calling", os.getenv("PROTOAGENT_OLLAMA_TOOL_CALLING", "auto"))
    if mode not in ("auto", "native", "json"):
        raise ValueError("Ollama tool_calling must be auto, native or json")
    if mode != "auto":
        return mode == "native"
    base_url = str(config.get("base_url") or "").rstrip("/")
    if not base_url or not model:
        return False
    key = (base_url, model)
    now = time.monotonic()
    cached = _OLLAMA_TOOL_CACHE.get(key)
    if cached and cached[0] > now:
        return cached[1]
    from .models import _get_json

    response = _get_json(base_url + "/api/show", timeout=1.0, payload={"model": model})
    metadata = response.get("data")
    capabilities = metadata.get("capabilities") if isinstance(metadata, dict) else None
    supported = (
        response.get("ok") is True and isinstance(capabilities, list) and "tools" in capabilities
    )
    if len(_OLLAMA_TOOL_CACHE) >= 128:
        _OLLAMA_TOOL_CACHE.clear()
    _OLLAMA_TOOL_CACHE[key] = (now + (600 if supported else 30), supported)
    return supported


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
        kwargs["supports_tool_calling"] = ollama_native_tools(cfg, selected_model)

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
    profile = llm_model_profile(requested, model)
    llm.configure_metrics(profile)
    fallbacks = cfg.get("fallback_models", [])
    if (
        not isinstance(fallbacks, list)
        or len(fallbacks) > 2
        or any(not isinstance(name, str) or not name.strip() for name in fallbacks)
    ):
        raise ValueError("fallback_models must contain at most two nonblank model names")
    selected = str(model or cfg.get("model") or "")
    if len(set(fallbacks)) != len(fallbacks) or selected in fallbacks:
        raise ValueError("Fallback models must be unique and different from the selected model")
    if fallbacks:
        from protolink import RoutedLLM

        models = {"selected": llm}
        for index, name in enumerate(fallbacks, start=1):
            candidate = create_llm(protolink_provider(requested), **llm_kwargs(requested, name))
            candidate.configure_metrics(llm_model_profile(requested, name))
            models[f"fallback-{index}"] = candidate
        llm = RoutedLLM(
            models,
            fallbacks=[key for key in models if key != "selected"],
            retries_per_model=0,
        )
        llm.configure_metrics(profile)
    return llm


def validate_protolink() -> dict[str, Any]:
    """Probe required native runtime APIs without starting agents or executing tools."""
    try:
        import protolink
        from protolink import (
            AgentGroup,
            AgentHooks,
            ApprovalBroker,
            ApprovalScope,
            CompletionCheck,
            CompletionValidator,
            ContextManifest,
            ContextPolicy,
            HistoryCompactor,
            LLMModelProfile,
            RedactionPolicy,
            RetryPolicy,
            RunHandle,
            RunRecorder,
            StateOperationResult,
            StorageCheckpointStore,
            SubagentLimits,
            TransportConfig,
            TransportLimits,
            TransportMetricsSnapshot,
        )
        from protolink.agents import Agent
        from protolink.client import AgentClient
        from protolink.llms.base import LLM
        from protolink.llms.factory import create_llm  # noqa: F401
        from protolink.logging import QuietLogger
        from protolink.security.auth import APIKeyAuth
        from protolink.storage import SQLiteRunStore
        from protolink.tools.builtins import ask_user_tool, filesystem_tools, process_tool
        from protolink.transport import Transport, TransportCapabilities, TransportRequestContext
        from protolink.transport.http_transport import HTTPTransport
        from protolink.transport.runtime_transport import RuntimeTransport

        streaming_ready = hasattr(Agent, "handle_task_streaming") and hasattr(
            AgentClient, "send_task_streaming"
        )
        metrics_ready = hasattr(LLM, "configure_metrics") and LLMModelProfile is not None
        compaction_ready = hasattr(LLM, "compact_history") and HistoryCompactor is not None
        context_manifest_ready = ContextManifest is not None
        run_report_ready = (
            RunRecorder is not None
            and "redaction_policy" in inspect.signature(SQLiteRunStore).parameters
            and "sensitive_values" in inspect.signature(RedactionPolicy).parameters
            and callable(getattr(StorageCheckpointStore, "list_changes", None))
        )
        state_ready = (
            StateOperationResult is not None
            and hasattr(Agent, "describe_state")
            and hasattr(AgentClient, "describe_state")
            and hasattr(Agent, "reset_state")
            and hasattr(AgentClient, "reset_state")
            and hasattr(Agent, "compact_state")
            and hasattr(AgentClient, "compact_state")
        )
        cancellation_ready = hasattr(RunHandle, "cancel") and hasattr(Agent, "cancel_task")
        execution_ready = all(
            callable(api)
            for api in (
                AgentGroup,
                ApprovalBroker,
                ApprovalScope,
                CompletionCheck,
                CompletionValidator,
                StorageCheckpointStore,
                filesystem_tools,
                process_tool,
                ask_user_tool,
            )
        )
        logging_ready = QuietLogger is not None
        user_input_ready = callable(ask_user_tool)
        agent_parameters = inspect.signature(Agent).parameters
        subagents_ready = (
            "subagents" in agent_parameters
            and "subagent_limits" in agent_parameters
            and callable(SubagentLimits)
            and callable(getattr(RunHandle, "spawn", None))
        )
        context_policy_ready = (
            "context_policy" in agent_parameters
            and "hooks" in agent_parameters
            and callable(ContextPolicy)
            and callable(AgentHooks)
        )
        http_transport_parameters = inspect.signature(HTTPTransport.__init__).parameters
        runtime_transport_parameters = inspect.signature(RuntimeTransport.__init__).parameters
        transport_ready = all(
            (
                TransportConfig is not None,
                TransportLimits is not None,
                RetryPolicy is not None,
                TransportCapabilities is not None,
                TransportMetricsSnapshot is not None,
                TransportRequestContext is not None,
                "config" in http_transport_parameters,
                "config" in runtime_transport_parameters,
                hasattr(Transport, "health"),
                hasattr(Transport, "metrics"),
                hasattr(TransportMetricsSnapshot, "to_dict"),
            )
        )
        auth_ready = (
            APIKeyAuth is not None
            and "authenticator" in agent_parameters
            and "credentials" in agent_parameters
            and "authenticator" in http_transport_parameters
            and "credentials" in http_transport_parameters
        )
        try:
            from protolink.tools import fetch_url, web_search

            web_tools = (web_search(), fetch_url())
            web_tools_ready = (
                [tool.name for tool in web_tools] == ["web_search", "fetch_url"]
                and all(tuple(tool.capabilities or ()) == ("network.read",) for tool in web_tools)
                and all(
                    str(getattr(tool, "_protolink_builtin_id", "")) == tool.name
                    for tool in web_tools
                )
            )
        except Exception:
            web_tools_ready = False
        try:
            from protolink.tools.adapters.mcp_adapter import MCPToolAdapter

            mcp_ready = all(
                hasattr(MCPToolAdapter, name)
                for name in ("session", "get_tools_async", "list_tools_async")
            )
        except ImportError:
            mcp_ready = False
        agent_ready = all(
            (
                streaming_ready,
                metrics_ready,
                compaction_ready,
                context_manifest_ready,
                run_report_ready,
                state_ready,
                cancellation_ready,
                logging_ready,
                auth_ready,
                transport_ready,
                execution_ready,
                subagents_ready,
                context_policy_ready,
                user_input_ready,
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
            "logging_ready": logging_ready,
            "auth_ready": auth_ready,
            "transport_ready": transport_ready,
            "web_tools_ready": web_tools_ready,
            "mcp_ready": mcp_ready,
            "subagents_ready": subagents_ready,
            "context_policy_ready": context_policy_ready,
            "user_input_ready": user_input_ready,
            "error": "" if agent_ready else "ProtoLink 0.8.0 runtime APIs are required",
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
                "logging_ready": False,
                "auth_ready": False,
                "transport_ready": False,
                "web_tools_ready": False,
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
                "logging_ready": False,
                "auth_ready": False,
                "transport_ready": False,
                "web_tools_ready": False,
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
