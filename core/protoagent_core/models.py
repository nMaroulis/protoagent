"""Model discovery for local and API providers."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .config import (
    API_PROVIDERS,
    ENV_KEYS,
    PROVIDER_LABELS,
    load_config,
    provider_config,
    visible_config,
)

API_MODEL_CHOICES = {
    "openai": [
        "gpt-5.2",
        "gpt-5.1",
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4o",
        "gpt-4o-mini",
        "o4-mini",
    ],
    "anthropic": [
        "claude-opus-4-20250514",
        "claude-sonnet-4-20250514",
        "claude-3-7-sonnet-latest",
        "claude-3-5-haiku-latest",
    ],
    "gemini": [
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-1.5-pro",
    ],
    "deepseek": [
        "deepseek-chat",
        "deepseek-reasoner",
    ],
}

MODEL_SCAN_DIRS = [
    "~/models",
    "~/Models",
    "~/.cache/lm-studio/models",
    "~/Library/Application Support/LM Studio/models",
    "~/.cache/huggingface/hub",
]

VALIDATION_CACHE_TTL_SECONDS = 600.0
VALIDATION_RETRY_TTL_SECONDS = 30.0
_VALIDATION_CACHE: dict[tuple[str, str, str, str], tuple[float, dict[str, str]]] = {}


def discover_models(validate_api_keys: bool = False) -> dict[str, Any]:
    """Return a serializable inventory of local and configured API models."""
    config = visible_config()
    active_provider = config.get("active_provider", "ollama")
    active_model = config.get("providers", {}).get(active_provider, {}).get("model", "")

    providers = [
        _discover_ollama(),
        _discover_lmstudio(),
        _discover_openai_compatible(),
        _discover_llamacpp_server(),
        _discover_llamacpp_local(),
    ]
    providers.extend(
        _api_provider(provider, validate_key=validate_api_keys)
        for provider in sorted(API_PROVIDERS - {"openai-compatible"})
    )

    return {
        "config_path": config["config_path"],
        "api_key_validation": validate_api_keys,
        "active_provider": active_provider,
        "active_model": active_model,
        "providers": providers,
    }


def _discover_ollama() -> dict[str, Any]:
    """Discover models from the Ollama HTTP API or CLI fallback."""
    cfg = provider_config("ollama")
    base_url = cfg.get("base_url") or "http://localhost:11434"
    response = _get_json(f"{base_url.rstrip('/')}/api/tags")
    models = []
    if response.get("ok"):
        for item in response.get("data", {}).get("models", []):
            models.append(
                _model(
                    item.get("name", ""),
                    source="ollama",
                    size_bytes=item.get("size"),
                    modified_at=item.get("modified_at"),
                    metadata=item.get("details", {}),
                )
            )
    else:
        models = _ollama_cli_models()

    return _provider(
        "ollama",
        kind="local-server",
        status="online" if response.get("ok") else ("detected" if models else "offline"),
        base_url=base_url,
        models=models,
        hint=response.get("error") if not response.get("ok") else "",
    )


def _discover_lmstudio() -> dict[str, Any]:
    """Discover models from an LM Studio OpenAI-compatible endpoint."""
    cfg = provider_config("lmstudio")
    base_url = (cfg.get("base_url") or "http://localhost:1234/v1").rstrip("/")
    response = _get_json(_openai_compatible_models_url(base_url))
    models = []
    if response.get("ok"):
        for item in response.get("data", {}).get("data", []):
            models.append(_model(item.get("id", ""), source="lmstudio", metadata=item))
    else:
        models = _scan_models("lmstudio")

    return _provider(
        "lmstudio",
        kind="openai-compatible-local",
        status="online" if response.get("ok") else ("detected" if models else "offline"),
        base_url=base_url,
        models=models,
        hint=response.get("error") if not response.get("ok") else "",
    )


def _discover_openai_compatible() -> dict[str, Any]:
    """Discover models from a generic OpenAI-compatible endpoint."""
    cfg = provider_config("openai-compatible")
    base_url = (cfg.get("base_url") or "http://localhost:1234/v1").rstrip("/")
    api_key = cfg.get("api_key", "")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
    response = _get_json(_openai_compatible_models_url(base_url), headers=headers)
    models = []
    if response.get("ok"):
        for item in response.get("data", {}).get("data", []):
            models.append(_model(item.get("id", ""), source="openai-compatible", metadata=item))

    key_status = _openai_compatible_key_status(api_key, response)
    return _provider(
        "openai-compatible",
        kind="openai-compatible-server",
        status="online" if response.get("ok") else "offline",
        base_url=base_url,
        models=models,
        hint=response.get("error") if not response.get("ok") else "",
        configured=bool(response.get("ok") or cfg.get("model") or api_key),
        api_key_set=bool(api_key),
        key_status=key_status,
        key_source=_key_source("openai-compatible"),
        env_key=ENV_KEYS.get("openai-compatible", ""),
    )


def _discover_llamacpp_server() -> dict[str, Any]:
    """Discover the model exposed by a llama.cpp HTTP server."""
    cfg = provider_config("llama.cpp-server")
    base_url = (cfg.get("base_url") or "http://localhost:8080").rstrip("/")
    response = _get_json(f"{base_url}/v1/models")
    models = []
    if response.get("ok"):
        for item in response.get("data", {}).get("data", []):
            models.append(_model(item.get("id", ""), source="llama.cpp-server", metadata=item))
    else:
        props = _get_json(f"{base_url}/props")
        if props.get("ok"):
            data = props.get("data", {})
            name = data.get("model_path") or data.get("default_generation_settings", {}).get("model")
            if name:
                models.append(_model(str(name), source="llama.cpp-server", metadata=data))
            response = props

    return _provider(
        "llama.cpp-server",
        kind="local-server",
        status="online" if response.get("ok") else "offline",
        base_url=base_url,
        models=models,
        hint=response.get("error") if not response.get("ok") else "",
    )


def _discover_llamacpp_local() -> dict[str, Any]:
    """Discover local GGUF models from common model directories."""
    models = _scan_models("llama.cpp-local")
    return _provider(
        "llama.cpp-local",
        kind="local-files",
        status="detected" if models else "not-found",
        base_url="",
        models=models,
        hint="Scans common model folders for .gguf files.",
    )


def _api_provider(provider: str, *, validate_key: bool = False) -> dict[str, Any]:
    """Build a cloud provider inventory card from config and defaults."""
    cfg = provider_config(provider)
    key = cfg.get("api_key") or os.getenv(ENV_KEYS.get(provider, ""), "")
    models = [_model(name, source=provider) for name in API_MODEL_CHOICES.get(provider, [])]
    key_status = "missing"
    status = "needs-key"
    configured = False
    hint = _api_key_setup_hint(provider)

    if key:
        configured = True
        key_status = "set"
        status = "configured"
        hint = "API key is present. Run `proto-cli models` to validate it."
        if validate_key:
            validation = _validate_api_key(
                provider,
                key,
                cfg.get("base_url", ""),
                cfg.get("model", ""),
            )
            key_status = validation["status"]
            hint = validation["hint"]
            if key_status == "valid":
                status = "configured"
            elif key_status == "invalid":
                status = "key-invalid"
                configured = False
            else:
                status = "key-unverified"

    return _provider(
        provider,
        kind="api",
        status=status,
        base_url=cfg.get("base_url", ""),
        models=models,
        hint=hint,
        configured=configured,
        api_key_set=bool(key),
        key_status=key_status,
        key_source=_key_source(provider),
        env_key=ENV_KEYS.get(provider, ""),
    )


def _get_json(url: str, timeout: float = 1.2, headers: dict[str, str] | None = None) -> dict[str, Any]:
    """Fetch JSON with a short timeout and return a status envelope."""
    request_headers = {"Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(url, headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8")
            return {"ok": True, "data": json.loads(text)}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": f"HTTP {exc.code}: {exc.reason}", "status_code": exc.code}
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": str(exc)}


def _validate_api_key(provider: str, api_key: str, base_url: str = "", model: str = "") -> dict[str, str]:
    """Validate an API key with a lightweight models endpoint request."""
    cache_key = _validation_cache_key(provider, api_key, base_url, model)
    cached = _cached_validation(cache_key)
    if cached is not None:
        return cached

    protolink_validation = _validate_with_protolink(provider)
    if protolink_validation["status"] == "valid":
        _store_validation(cache_key, protolink_validation)
        return protolink_validation

    request = _api_key_validation_request(provider, api_key, base_url)
    if request is None:
        _store_validation(cache_key, protolink_validation)
        return protolink_validation

    url, headers = request
    response = _get_json(url, timeout=2.0, headers=headers)
    if response.get("ok"):
        validation = {"status": "valid", "hint": "API key validated against the provider models endpoint."}
        _store_validation(cache_key, validation)
        return validation
    if response.get("status_code") in {401, 403}:
        validation = {"status": "invalid", "hint": "API key was rejected by the provider."}
        _store_validation(cache_key, validation)
        return validation
    error = str(response.get("error", "provider did not return a validation response"))
    if protolink_validation["hint"]:
        error = f"{error}; {protolink_validation['hint']}"
    validation = {"status": "unverified", "hint": f"API key present, but validation was inconclusive: {error}"}
    _store_validation(cache_key, validation)
    return validation


def _validation_cache_key(
    provider: str,
    api_key: str,
    base_url: str = "",
    model: str = "",
) -> tuple[str, str, str, str]:
    """Return a cache key without storing the raw API key."""
    fingerprint = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    return (provider, fingerprint, base_url or "", model or "")


def _cached_validation(cache_key: tuple[str, str, str, str]) -> dict[str, str] | None:
    """Return a recent provider validation result if it is still fresh."""
    cached = _VALIDATION_CACHE.get(cache_key)
    if cached is None:
        return None
    timestamp, validation = cached
    ttl = VALIDATION_CACHE_TTL_SECONDS if validation.get("status") == "valid" else VALIDATION_RETRY_TTL_SECONDS
    if time.monotonic() - timestamp > ttl:
        _VALIDATION_CACHE.pop(cache_key, None)
        return None
    cached_validation = dict(validation)
    if cached_validation.get("status") == "valid":
        cached_validation["hint"] = "API key/model validation reused from recent check."
    return cached_validation


def _store_validation(cache_key: tuple[str, str, str, str], validation: dict[str, str]) -> None:
    """Cache provider validation briefly so opening /models stays instant."""
    _VALIDATION_CACHE[cache_key] = (time.monotonic(), dict(validation))


def remember_valid_provider(provider: str, model: str = "", base_url: str = "") -> None:
    """Record that a live model call succeeded for this provider/model."""
    cfg = provider_config(provider)
    provider_id = cfg.get("id", provider)
    if provider_id not in API_PROVIDERS:
        return
    api_key = cfg.get("api_key") or os.getenv(ENV_KEYS.get(provider_id, ""), "")
    if not api_key:
        return
    cache_key = _validation_cache_key(
        provider_id,
        api_key,
        base_url or cfg.get("base_url", ""),
        model or cfg.get("model", ""),
    )
    _store_validation(
        cache_key,
        {
            "status": "valid",
            "hint": "API key/model recently succeeded in a live ProtoAgent run.",
        },
    )


def _validate_with_protolink(provider: str) -> dict[str, str]:
    """Validate provider connectivity through Protolink when available."""
    try:
        from .llm import create_llm_from_config

        llm = create_llm_from_config(provider)
        if llm.validate_connection():
            return {
                "status": "valid",
                "hint": "API key and selected model validated through Protolink.",
            }
        return {
            "status": "unverified",
            "hint": "Protolink could not validate the key/model combination.",
        }
    except Exception as exc:
        return {
            "status": "unverified",
            "hint": f"Protolink validation unavailable: {exc}",
        }


def _api_key_validation_request(
    provider: str,
    api_key: str,
    base_url: str = "",
) -> tuple[str, dict[str, str]] | None:
    """Return the endpoint and headers used to validate a provider key."""
    if provider == "openai":
        return "https://api.openai.com/v1/models", {"Authorization": f"Bearer {api_key}"}
    if provider == "anthropic":
        return (
            "https://api.anthropic.com/v1/models",
            {"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        )
    if provider == "gemini":
        query = urllib.parse.urlencode({"key": api_key})
        return f"https://generativelanguage.googleapis.com/v1beta/models?{query}", {}
    if provider == "deepseek":
        endpoint = _openai_compatible_models_url((base_url or "https://api.deepseek.com").rstrip("/"))
        return endpoint, {"Authorization": f"Bearer {api_key}"}
    return None


def _api_key_setup_hint(provider: str) -> str:
    """Return a concise setup hint for a missing API key."""
    env_key = ENV_KEYS.get(provider, "")
    if env_key:
        return f"Set with `proto-cli key {provider}`, the TUI /key command, or {env_key}."
    return f"Set with `proto-cli key {provider}` or the TUI /key command."


def _key_source(provider: str) -> str:
    """Return where the resolved API key came from, without exposing it."""
    cfg = load_config().get("providers", {}).get(provider, {})
    if cfg.get("api_key"):
        return "config"
    if os.getenv(ENV_KEYS.get(provider, ""), ""):
        return "env"
    return ""


def _openai_compatible_key_status(api_key: str, response: dict[str, Any]) -> str:
    """Describe generic OpenAI-compatible key state from the probe response."""
    if response.get("ok"):
        return "valid" if api_key else "not-required"
    if response.get("status_code") in {401, 403}:
        return "invalid"
    return "set" if api_key else "missing"


def _openai_compatible_models_url(base_url: str) -> str:
    """Return the models endpoint for an OpenAI-compatible base URL."""
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        return f"{root}/models"
    return f"{root}/v1/models"


def _ollama_cli_models() -> list[dict[str, Any]]:
    """Read installed Ollama models from the `ollama list` command."""
    try:
        result = subprocess.run(
            ["ollama", "list"],
            check=False,
            capture_output=True,
            text=True,
            timeout=1.5,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if len(lines) <= 1:
        return []
    models = []
    for line in lines[1:]:
        name = line.split()[0]
        if name:
            models.append(_model(name, source="ollama-cli"))
    return models


def _scan_models(source: str, limit: int = 80) -> list[dict[str, Any]]:
    """Scan common directories for GGUF model files."""
    found: list[dict[str, Any]] = []
    for raw_dir in MODEL_SCAN_DIRS:
        root = Path(raw_dir).expanduser()
        if not root.exists():
            continue
        try:
            iterator = root.rglob("*.gguf")
            for path in iterator:
                found.append(
                    _model(
                        str(path),
                        source=source,
                        size_bytes=path.stat().st_size if path.exists() else None,
                    )
                )
                if len(found) >= limit:
                    return found
        except OSError:
            continue
    return found


def _provider(
    provider_id: str,
    *,
    kind: str,
    status: str,
    base_url: str,
    models: list[dict[str, Any]],
    hint: str = "",
    configured: bool | None = None,
    api_key_set: bool = False,
    key_status: str = "",
    key_source: str = "",
    env_key: str = "",
) -> dict[str, Any]:
    """Create a normalized provider inventory record."""
    return {
        "id": provider_id,
        "name": PROVIDER_LABELS.get(provider_id, provider_id),
        "kind": kind,
        "status": status,
        "configured": bool(configured) if configured is not None else status in {"online", "detected", "configured"},
        "base_url": base_url,
        "hint": hint,
        "api_key_set": api_key_set,
        "key_status": key_status,
        "key_source": key_source,
        "env_key": env_key,
        "models": [model for model in models if model.get("id")],
    }


def _model(
    model_id: str,
    *,
    source: str,
    size_bytes: int | None = None,
    modified_at: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a normalized model inventory record."""
    return {
        "id": str(model_id),
        "source": source,
        "size_bytes": size_bytes,
        "size_label": _size_label(size_bytes),
        "modified_at": modified_at or "",
        "metadata": metadata or {},
    }


def _size_label(size_bytes: int | None) -> str:
    """Format a byte count into a compact human-readable label."""
    if not size_bytes:
        return ""
    size = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return ""
