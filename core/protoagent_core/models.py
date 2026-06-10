"""Model discovery for local and API providers."""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .config import (
    API_PROVIDERS,
    ENV_KEYS,
    PROVIDER_LABELS,
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


def discover_models() -> dict[str, Any]:
    """Return a serializable inventory of local and configured API models."""
    config = visible_config()
    active_provider = config.get("active_provider", "ollama")
    active_model = config.get("providers", {}).get(active_provider, {}).get("model", "")

    providers = [
        _discover_ollama(),
        _discover_lmstudio(),
        _discover_llamacpp_server(),
        _discover_llamacpp_local(),
    ]
    providers.extend(_api_provider(provider) for provider in sorted(API_PROVIDERS))

    return {
        "config_path": config["config_path"],
        "active_provider": active_provider,
        "active_model": active_model,
        "providers": providers,
    }


def _discover_ollama() -> dict[str, Any]:
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
    cfg = provider_config("lmstudio")
    base_url = (cfg.get("base_url") or "http://localhost:1234/v1").rstrip("/")
    response = _get_json(f"{base_url}/models")
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


def _discover_llamacpp_server() -> dict[str, Any]:
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
    models = _scan_models("llama.cpp-local")
    return _provider(
        "llama.cpp-local",
        kind="local-files",
        status="detected" if models else "not-found",
        base_url="",
        models=models,
        hint="Scans common model folders for .gguf files.",
    )


def _api_provider(provider: str) -> dict[str, Any]:
    cfg = provider_config(provider)
    key = cfg.get("api_key") or os.getenv(ENV_KEYS.get(provider, ""), "")
    models = [_model(name, source=provider) for name in API_MODEL_CHOICES.get(provider, [])]
    return _provider(
        provider,
        kind="api",
        status="configured" if key else "needs-key",
        base_url=cfg.get("base_url", ""),
        models=models,
        hint="Add an API key, then choose a built-in or custom model.",
        configured=bool(key),
    )


def _get_json(url: str, timeout: float = 1.2) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8")
            return {"ok": True, "data": json.loads(text)}
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": str(exc)}


def _ollama_cli_models() -> list[dict[str, Any]]:
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
) -> dict[str, Any]:
    return {
        "id": provider_id,
        "name": PROVIDER_LABELS.get(provider_id, provider_id),
        "kind": kind,
        "status": status,
        "configured": bool(configured) if configured is not None else status in {"online", "detected", "configured"},
        "base_url": base_url,
        "hint": hint,
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
    return {
        "id": str(model_id),
        "source": source,
        "size_bytes": size_bytes,
        "size_label": _size_label(size_bytes),
        "modified_at": modified_at or "",
        "metadata": metadata or {},
    }


def _size_label(size_bytes: int | None) -> str:
    if not size_bytes:
        return ""
    size = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return ""
