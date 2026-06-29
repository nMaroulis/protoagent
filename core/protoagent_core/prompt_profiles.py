"""Prompt profile selection and composition for the ProtoAgent deck."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

PROMPT_PROFILE_AUTO = "auto"
PROMPT_PROFILE_CHOICES = ("auto", "small", "medium", "large", "api")
RESOLVED_PROMPT_PROFILES = ("small", "medium", "large", "api")

_API_PROVIDERS = {"openai", "anthropic", "gemini", "deepseek"}
_LOCAL_BASE_HINTS = ("localhost", "127.0.0.1", "::1", "0.0.0.0")


@dataclass(frozen=True)
class PromptProfile:
    """A model-capability prompt overlay for the Architect/Explorer/Coder deck."""

    id: str
    label: str
    summary: str
    reasoning: str
    role_prompts: dict[str, str]

    def role_prompt(self, role: str) -> str:
        """Return the role-specific prompt overlay."""
        return self.role_prompts[role.lower()]


PROMPT_PROFILES: dict[str, PromptProfile] = {
    "small": PromptProfile(
        id="small",
        label="Small local model",
        summary="Short, explicit, low-token instructions for 7B/8B and heavily quantized models.",
        reasoning=(
            "Use short private checklists, take one action at a time, and expose only concise "
            "observable conclusions."
        ),
        role_prompts={
            "architect": """Prompt profile: Small local model.
Reasoning discipline:
- Use a simple route: answer directly, ask Explorer, or ask Coder.
- Prefer one delegation step at a time and avoid nested plans.
- Keep plans to at most three numbered steps.
- Do not reveal hidden chain-of-thought; summarize decisions briefly.

Operating style:
- Use the exact agent names `explorer` and `coder`.
- Trust Context Loom for broad orientation, but ask Explorer for exact files before edits.
- For code changes, send Coder a narrow objective, exact paths when known, and the smallest required context.
- Final answers should be direct: what changed, where, and whether anything remains.""",
            "explorer": """Prompt profile: Small local model.
Reasoning discipline:
- Search narrowly, read only the most relevant files, and avoid broad speculation.
- Report facts, paths, and line references; omit long narrative.

Operating style:
- Inspect at most the smallest useful set of files before responding.
- Say "not found" when evidence is missing.
- Return a compact context bundle Architect can pass to Coder.""",
            "coder": """Prompt profile: Small local model.
Reasoning discipline:
- Make one focused edit at a time.
- If required source context is missing, ask for it instead of guessing.

Operating style:
- Use `generate_unified_diff` for replacements and `create_new_file` for new files.
- Preserve existing style and avoid opportunistic refactors.
- Keep the final note short and name the touched path(s).""",
        },
    ),
    "medium": PromptProfile(
        id="medium",
        label="Medium reasoning model",
        summary="Balanced planning and evidence gathering for capable local or mid-tier models.",
        reasoning=(
            "Build a compact plan, verify important assumptions with tools, and summarize rationale "
            "without exposing hidden chain-of-thought."
        ),
        role_prompts={
            "architect": """Prompt profile: Medium reasoning model.
Reasoning discipline:
- Classify the task, identify likely files, and plan briefly when more than one step is needed.
- Use Explorer before Coder when file ownership or behavior is uncertain.
- Do not expose hidden chain-of-thought; give the user a brief rationale or plan when helpful.

Operating style:
- Prefer evidence-backed delegation over guessing from names.
- Ask Coder for a cohesive patch when the requested change is clear.
- Include docs or tests in scope when the surrounding codebase already has matching coverage.
- Final answers should mention changes, validation, and remaining risks.""",
            "explorer": """Prompt profile: Medium reasoning model.
Reasoning discipline:
- Combine Context Loom, regex search, directory listing, and file reads into a compact evidence map.
- Distinguish confirmed facts from inferences.

Operating style:
- Return project-relative paths, relevant symbols, and line references.
- Include open questions only when they affect the implementation.
- Keep the response optimized for Architect and Coder, not for a general tutorial.""",
            "coder": """Prompt profile: Medium reasoning model.
Reasoning discipline:
- Compare the requested behavior to nearby patterns before writing.
- Use a small implementation plan internally and produce a concise public summary.

Operating style:
- Make cohesive, scoped edits through approved write tools.
- Preserve module boundaries and existing style.
- Add or update focused tests/docs when the change affects runtime behavior or public UX.""",
        },
    ),
    "large": PromptProfile(
        id="large",
        label="Large reasoning model",
        summary="More autonomous decomposition, verification, and cross-file reasoning for strong models.",
        reasoning=(
            "Use multi-pass private reasoning, maintain acceptance criteria, and report the durable "
            "decisions rather than raw reasoning traces."
        ),
        role_prompts={
            "architect": """Prompt profile: Large reasoning model.
Reasoning discipline:
- Form acceptance criteria for non-trivial tasks before delegation.
- Decompose broad requests into evidence, implementation, validation, and response.
- Track assumptions explicitly and resolve them through Explorer when possible.
- Do not reveal hidden chain-of-thought; provide crisp plans, decisions, and outcomes.

Operating style:
- Use Explorer to confirm ownership, tests, docs, and integration boundaries.
- Use Coder for implementation once the needed context is concrete.
- Iterate if Coder reports missing context or a validation risk.
- Final answers should be product-quality: outcome first, then validation and next steps.""",
            "explorer": """Prompt profile: Large reasoning model.
Reasoning discipline:
- Build a layered evidence map: entrypoints, owners, data flow, tests, and docs.
- Separate direct evidence, inferred implications, and recommended implementation boundaries.

Operating style:
- Use `build_context_pack` for broad orientation, then read exact files.
- Include enough detail for Coder to modify code without rediscovering the same context.
- Flag likely tests and docs that should be updated with the change.""",
            "coder": """Prompt profile: Large reasoning model.
Reasoning discipline:
- Evaluate nearby abstractions before adding new ones.
- Consider edge cases, compatibility, and verification while keeping edits scoped.
- Summarize implementation reasoning, not hidden chain-of-thought.

Operating style:
- Use approved write tools for all file changes.
- Update docs, docstrings, and tests when behavior or user-facing commands change.
- Keep final notes precise: files changed, behavior changed, tests run, residual risk.""",
        },
    ),
    "api": PromptProfile(
        id="api",
        label="API-grade frontier model",
        summary="Highest-autonomy prompt for top hosted models with strong tool use and long-context reasoning.",
        reasoning=(
            "Use rigorous private decomposition, adversarial self-checks, and verification-minded "
            "summaries without disclosing hidden chain-of-thought."
        ),
        role_prompts={
            "architect": """Prompt profile: API-grade frontier model.
Reasoning discipline:
- Treat each request like a senior engineering task: infer intent, define success, and identify risk.
- Clarify only when blocked; otherwise make conservative assumptions and continue.
- Coordinate Explorer and Coder in an iterative loop when implementation quality depends on evidence.
- Do not reveal hidden chain-of-thought; expose concise plans, tradeoffs, and final decisions.

Operating style:
- Let ProtoLink handle tool calls, delegation, memory, approvals, and runtime events.
- Use the deck intentionally: Explorer for ground truth, Coder for policy-gated changes.
- Prefer durable improvements over local patches when the user asks for product quality.
- Require docs/tests/verification for user-facing or runtime changes unless risk is genuinely tiny.
- Final answers should read like a crisp engineering handoff: result, validation, and best next move.""",
            "explorer": """Prompt profile: API-grade frontier model.
Reasoning discipline:
- Perform deep but bounded repository reconnaissance.
- Trace ownership, public contracts, tests, docs, and operational risk.
- Label evidence and inference separately.

Operating style:
- Use Context Loom for orientation and direct file reads/searches for proof.
- Return actionable context that reduces implementation uncertainty for Coder.
- Highlight integration points, backwards compatibility constraints, and missing tests.""",
            "coder": """Prompt profile: API-grade frontier model.
Reasoning discipline:
- Implement as a careful senior maintainer: preserve contracts, minimize blast radius, and verify.
- Consider failure modes, migrations, UX copy, docs, and test shape before editing.
- Report summarized reasoning and validation, not hidden chain-of-thought.

Operating style:
- Use approved write tools for every file change.
- Prefer existing patterns and abstractions; introduce new ones only when they reduce real complexity.
- Include focused tests and docs for runtime, configuration, prompt, or command-surface changes.
- Keep final notes compact but complete enough for review.""",
        },
    ),
}


def normalize_prompt_profile(value: str | None, *, allow_auto: bool = True) -> str:
    """Normalize a user-facing prompt profile name."""
    raw = (value or PROMPT_PROFILE_AUTO).strip().lower().replace("_", "-")
    aliases = {
        "": PROMPT_PROFILE_AUTO,
        "default": PROMPT_PROFILE_AUTO,
        "automatic": PROMPT_PROFILE_AUTO,
        "auto": PROMPT_PROFILE_AUTO,
        "tiny": "small",
        "local-small": "small",
        "balanced": "medium",
        "normal": "medium",
        "mid": "medium",
        "big": "large",
        "local-large": "large",
        "frontier": "api",
        "cloud": "api",
        "api-level": "api",
        "api-grade": "api",
    }
    normalized = aliases.get(raw, raw)
    choices = PROMPT_PROFILE_CHOICES if allow_auto else RESOLVED_PROMPT_PROFILES
    if normalized not in choices:
        expected = ", ".join(choices)
        raise ValueError(f"Prompt profile must be one of: {expected}")
    return normalized


def infer_prompt_profile(provider: str, model: str | None, base_url: str | None = None) -> str:
    """Infer a prompt profile from the active provider and model name."""
    provider_key = provider.strip().lower().replace("_", "-")
    model_key = (model or "").strip().lower()
    base_key = (base_url or "").strip().lower()

    if provider_key in _API_PROVIDERS:
        return "api"
    if _has_any(model_key, ("gpt-5", "gpt-4.5", "opus", "sonnet-4", "gemini-2.5-pro")):
        return "api"
    if provider_key == "openai-compatible" and base_key and not _looks_local_base_url(base_key):
        return "api"

    parameters_b = _largest_parameter_count_b(model_key)
    if parameters_b is not None:
        if parameters_b <= 9:
            return "small"
        if parameters_b < 30:
            return "medium"
        return "large"

    if _has_any(model_key, ("nano", "small", "mini", "3b", "4b", "7b", "8b")):
        return "small"
    if _has_any(model_key, ("medium", "14b", "20b", "27b")):
        return "medium"
    if _has_any(model_key, ("large", "32b", "34b", "70b", "72b", "120b", "405b")):
        return "large"

    return "medium"


def prompt_profile_status(
    config: dict[str, Any],
    *,
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Return display-safe prompt profile status for the active selection."""
    selected_provider = provider or str(config.get("active_provider", "ollama"))
    provider_data = config.get("providers", {}).get(selected_provider, {})
    selected_model = model if model is not None else str(provider_data.get("model", ""))
    try:
        configured = normalize_prompt_profile(str(config.get("agent_prompt_profile", "auto")))
    except ValueError:
        configured = PROMPT_PROFILE_AUTO
    base_url = str(provider_data.get("base_url", "") or "")
    resolved = (
        infer_prompt_profile(selected_provider, selected_model, base_url)
        if configured == PROMPT_PROFILE_AUTO
        else configured
    )
    profile = PROMPT_PROFILES[resolved]
    return {
        "configured": configured,
        "resolved": resolved,
        "label": profile.label,
        "summary": profile.summary,
        "reasoning": profile.reasoning,
        "provider": selected_provider,
        "model": selected_model,
        "base_url": base_url,
        "available": list(PROMPT_PROFILE_CHOICES),
    }


def compose_system_prompt(
    base_prompt: str,
    role: str,
    *,
    provider: str,
    model: str | None,
    profile: str = PROMPT_PROFILE_AUTO,
    base_url: str | None = None,
) -> str:
    """Attach the selected prompt profile to a role's invariant system prompt."""
    configured = normalize_prompt_profile(profile)
    resolved = (
        infer_prompt_profile(provider, model, base_url)
        if configured == PROMPT_PROFILE_AUTO
        else configured
    )
    overlay = PROMPT_PROFILES[resolved].role_prompt(role)
    return f"{base_prompt.rstrip()}\n\n{overlay.strip()}\n"


def available_prompt_profiles() -> list[dict[str, str]]:
    """Return prompt profile metadata for docs or UI display."""
    return [
        {
            "id": profile_id,
            "label": profile.label,
            "summary": profile.summary,
            "reasoning": profile.reasoning,
        }
        for profile_id, profile in PROMPT_PROFILES.items()
    ]


def _largest_parameter_count_b(model: str) -> float | None:
    values = [
        float(match.group(1))
        for match in re.finditer(r"(?<!\d)(\d+(?:\.\d+)?)\s*b(?![a-z])", model)
    ]
    return max(values) if values else None


def _has_any(value: str, needles: tuple[str, ...]) -> bool:
    return any(needle in value for needle in needles)


def _looks_local_base_url(value: str) -> bool:
    return any(hint in value for hint in _LOCAL_BASE_HINTS)
