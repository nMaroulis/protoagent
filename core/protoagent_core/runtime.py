"""Runtime model calls for the CLI.

This layer deliberately uses provider HTTP APIs directly for the interactive
CLI path. The protolink agent factories still live in agents.py for the full
A2A mesh, but a user who selects a model should get a response immediately.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .config import load_config, normalize_provider, provider_config

DEFAULT_TIMEOUT = 300

SYSTEM_PROMPT = """You are ProtoAgent, a local-first coding assistant.

Be warm, concise, and useful. For casual conversation, answer naturally.
For coding requests, explain what you can do and avoid claiming that files
were changed unless a tool/action payload actually did it.
"""


def run_selected_model(prompt: str) -> dict[str, Any]:
    config = load_config()
    provider = normalize_provider(config.get("active_provider", "ollama"))
    cfg = provider_config(provider, config)
    model = cfg.get("model", "")
    if not model:
        raise RuntimeError(f"No model selected for provider '{provider}'")

    if provider == "ollama":
        answer = _ollama_chat(cfg, model, prompt)
    elif provider in {"lmstudio", "llama.cpp-server"}:
        answer = _openai_compatible_chat(cfg, model, prompt, default_base_url=cfg.get("base_url", ""))
    elif provider == "openai":
        answer = _openai_compatible_chat(cfg, model, prompt, default_base_url="https://api.openai.com/v1")
    elif provider == "deepseek":
        answer = _openai_compatible_chat(cfg, model, prompt, default_base_url="https://api.deepseek.com/v1")
    elif provider == "anthropic":
        answer = _anthropic_chat(cfg, model, prompt)
    elif provider == "gemini":
        answer = _gemini_chat(cfg, model, prompt)
    else:
        raise RuntimeError(f"Provider '{provider}' is not executable from the CLI yet")

    cleaned = answer.strip()
    if not cleaned:
        cleaned = "(model returned an empty response)"

    return {
        "provider": provider,
        "model": model,
        "answer": cleaned,
    }


def _ollama_chat(cfg: dict[str, Any], model: str, prompt: str) -> str:
    base_url = (cfg.get("base_url") or "http://localhost:11434").rstrip("/")
    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    }
    data = _post_json(f"{base_url}/api/chat", payload)
    if data.get("error"):
        raise RuntimeError(data["error"])
    return data.get("message", {}).get("content", "")


def _openai_compatible_chat(
    cfg: dict[str, Any],
    model: str,
    prompt: str,
    *,
    default_base_url: str,
) -> str:
    base_url = (cfg.get("base_url") or default_base_url).rstrip("/")
    if base_url.endswith("/v1"):
        url = f"{base_url}/chat/completions"
    else:
        url = f"{base_url}/v1/chat/completions"

    headers = {}
    api_key = cfg.get("api_key")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    }
    data = _post_json(url, payload, headers=headers)
    choices = data.get("choices", [])
    if not choices:
        return ""
    return choices[0].get("message", {}).get("content", "")


def _anthropic_chat(cfg: dict[str, Any], model: str, prompt: str) -> str:
    api_key = cfg.get("api_key")
    if not api_key:
        raise RuntimeError("Anthropic API key is not set")
    payload = {
        "model": model,
        "max_tokens": 2048,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt}],
    }
    data = _post_json(
        "https://api.anthropic.com/v1/messages",
        payload,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    parts = data.get("content", [])
    return "".join(part.get("text", "") for part in parts if part.get("type") == "text")


def _gemini_chat(cfg: dict[str, Any], model: str, prompt: str) -> str:
    api_key = cfg.get("api_key")
    if not api_key:
        raise RuntimeError("Gemini API key is not set")
    quoted_model = urllib.parse.quote(model, safe="")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{quoted_model}:generateContent?key={api_key}"
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
    }
    data = _post_json(url, payload)
    candidates = data.get("candidates", [])
    if not candidates:
        return ""
    parts = candidates[0].get("content", {}).get("parts", [])
    return "".join(part.get("text", "") for part in parts)


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        **(headers or {}),
    }
    request = urllib.request.Request(url, data=body, headers=request_headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT) as response:
            text = response.read().decode("utf-8")
            return json.loads(text)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{url} returned HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{url} failed: {exc}") from exc
