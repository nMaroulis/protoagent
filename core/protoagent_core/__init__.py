"""ProtoAgent core package.

The Rust CLI calls into this package through PyO3.  Keep all provider
discovery, configuration, tools, and protolink agent wiring here so other
frontends can share the same brain.
"""

from .agent_engine import (
    add_api_key,
    answer_help_question,
    configure_agent_prompt_profile,
    configure_context_window,
    doctor,
    get_agent_prompt_profile,
    get_config,
    get_context_settings,
    list_models,
    list_quality_eval_tasks,
    process_prompt,
    run_quality_eval,
    set_model,
)

__all__ = [
    "add_api_key",
    "answer_help_question",
    "configure_agent_prompt_profile",
    "configure_context_window",
    "doctor",
    "get_agent_prompt_profile",
    "get_context_settings",
    "get_config",
    "list_models",
    "list_quality_eval_tasks",
    "process_prompt",
    "run_quality_eval",
    "set_model",
]
