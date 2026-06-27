"""Configuration helpers for ProtoAgent providers and model selection."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

CONFIG_VERSION = 1
CONFIG_DIR = Path(
    os.getenv("PROTOAGENT_CONFIG_DIR", os.getenv("PROTOAGENT_HOME", "~/.protoagent"))
).expanduser()
CONFIG_PATH = CONFIG_DIR / "config.json"
MIN_CONTEXT_WINDOW = 2_048
MAX_CONTEXT_WINDOW = 2_097_152

API_PROVIDERS = {"openai", "anthropic", "gemini", "deepseek", "openai-compatible"}
LOCAL_PROVIDERS = {"ollama", "lmstudio", "llama.cpp-server", "llama.cpp-local"}

ENV_KEYS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "openai-compatible": "OPENAI_COMPATIBLE_API_KEY",
}

DEFAULT_MODELS = {
    "ollama": "",
    "lmstudio": "",
    "llama.cpp-server": "llama3",
    "llama.cpp-local": "",
    "openai": "gpt-5.2",
    "anthropic": "claude-sonnet-4-20250514",
    "gemini": "gemini-2.5-pro",
    "deepseek": "deepseek-chat",
    "openai-compatible": "",
}

DEFAULT_BASE_URLS = {
    "ollama": os.getenv("OLLAMA_URL", os.getenv("OLLAMA_HOST", "http://localhost:11434")),
    "lmstudio": os.getenv("LMSTUDIO_URL", "http://localhost:1234/v1"),
    "llama.cpp-server": os.getenv("LLAMACPP_SERVER_URL", "http://localhost:8080"),
    "llama.cpp-local": "",
    "openai": "",
    "anthropic": "",
    "gemini": "",
    "deepseek": "https://api.deepseek.com",
    "openai-compatible": os.getenv("OPENAI_COMPATIBLE_BASE_URL", "http://localhost:1234/v1"),
}

PROVIDER_LABELS = {
    "ollama": "Ollama",
    "lmstudio": "LM Studio",
    "llama.cpp-server": "llama.cpp server",
    "llama.cpp-local": "llama.cpp local",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "gemini": "Gemini",
    "deepseek": "DeepSeek",
    "openai-compatible": "OpenAI compatible",
}


def default_config() -> dict[str, Any]:
    """Create the baseline provider configuration."""
    providers: dict[str, dict[str, Any]] = {}
    for provider in [*sorted(LOCAL_PROVIDERS), *sorted(API_PROVIDERS)]:
        providers[provider] = {
            "label": PROVIDER_LABELS[provider],
            "base_url": DEFAULT_BASE_URLS.get(provider, ""),
            "model": DEFAULT_MODELS.get(provider, ""),
            "api_key": "",
        }

    return {
        "version": CONFIG_VERSION,
        "active_provider": "ollama",
        "providers": providers,
    }


def load_config() -> dict[str, Any]:
    """Load config and merge with defaults so new providers appear cleanly."""
    config = default_config()
    if CONFIG_PATH.exists():
        try:
            with CONFIG_PATH.open("r", encoding="utf-8") as handle:
                existing = json.load(handle)
            config = _deep_merge(config, existing)
        except (OSError, json.JSONDecodeError):
            # Keep a usable default config; doctor() reports the path so the
            # user can inspect or remove a malformed file.
            pass
    return config


def save_config(config: dict[str, Any]) -> None:
    """Persist provider configuration with user-only file permissions."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with CONFIG_PATH.open("w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2, sort_keys=True)
        handle.write("\n")
    try:
        CONFIG_PATH.chmod(0o600)
    except OSError:
        pass


def set_api_key(provider: str, api_key: str) -> dict[str, Any]:
    """Store an API key for a supported cloud provider."""
    provider = normalize_provider(provider)
    if provider not in API_PROVIDERS:
        raise ValueError(f"{provider} does not use a cloud API key")
    if not api_key.strip():
        raise ValueError("API key cannot be empty")

    config = load_config()
    config["providers"][provider]["api_key"] = api_key.strip()
    config["active_provider"] = provider
    save_config(config)
    return visible_config(config)


def set_active_model(provider: str, model: str, base_url: str | None = None) -> dict[str, Any]:
    """Set the active provider/model pair used by the agent runtime."""
    provider = normalize_provider(provider)
    if provider not in load_config()["providers"]:
        raise ValueError(f"Unknown provider: {provider}")
    if not model.strip():
        raise ValueError("Model cannot be empty")

    config = load_config()
    config["active_provider"] = provider
    config["providers"][provider]["model"] = model.strip()
    if base_url is not None and base_url.strip():
        config["providers"][provider]["base_url"] = base_url.strip()
    save_config(config)
    return visible_config(config)


def set_context_window(provider: str, window_tokens: int | None) -> dict[str, Any]:
    """Persist or clear the runtime context window for a controllable provider."""
    provider = normalize_provider(provider)
    if provider != "ollama":
        raise ValueError("Context-window control is currently available for Ollama models")
    if window_tokens is not None and not MIN_CONTEXT_WINDOW <= window_tokens <= MAX_CONTEXT_WINDOW:
        raise ValueError(
            f"Context window must be between {MIN_CONTEXT_WINDOW} and {MAX_CONTEXT_WINDOW} tokens"
        )

    config = load_config()
    target = config["providers"][provider]
    if window_tokens is None:
        target.pop("context_window", None)
    else:
        target["context_window"] = int(window_tokens)
    save_config(config)
    return visible_config(config)


def provider_config(
    provider: str | None = None, config: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Return a provider config with environment API keys resolved."""
    config = config or load_config()
    provider = normalize_provider(provider or config.get("active_provider", "ollama"))
    data = deepcopy(config["providers"].get(provider, {}))
    data["id"] = provider
    data["api_key"] = data.get("api_key") or os.getenv(ENV_KEYS.get(provider, ""), "")
    return data


def visible_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return configuration safe for terminal display."""
    config = deepcopy(config or load_config())
    for provider, data in config.get("providers", {}).items():
        stored_key = data.get("api_key", "")
        env_key = os.getenv(ENV_KEYS.get(provider, ""), "")
        key = stored_key or env_key
        data["api_key_set"] = bool(key)
        data["api_key"] = redact_key(key) if key else ""
        data["from_env"] = bool(env_key and not stored_key)
    config["config_path"] = str(CONFIG_PATH)
    return config


def redact_key(value: str) -> str:
    """Mask an API key while keeping enough shape for recognition."""
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def normalize_provider(provider: str) -> str:
    """Normalize provider aliases to canonical config identifiers."""
    normalized = provider.strip().lower().replace("_", "-")
    aliases = {
        "lm-studio": "lmstudio",
        "llamacpp": "llama.cpp-server",
        "llama-cpp": "llama.cpp-server",
        "llama.cpp": "llama.cpp-server",
        "llama.cpp-local": "llama.cpp-local",
        "llama-cpp-local": "llama.cpp-local",
        "openai-compatible": "openai-compatible",
        "openai-compatible-server": "openai-compatible",
        "openai-compatible-local": "openai-compatible",
        "openai-compatible-api": "openai-compatible",
    }
    return aliases.get(normalized, normalized)


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge user config over defaults."""
    merged = deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged
