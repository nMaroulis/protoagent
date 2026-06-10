"""ProtoAgent core package.

The Rust CLI calls into this package through PyO3.  Keep all provider
discovery, configuration, tools, and protolink agent wiring here so other
frontends can share the same brain.
"""

from .agent_engine import (
    add_api_key,
    doctor,
    get_config,
    list_models,
    process_prompt,
    set_model,
)

__all__ = [
    "add_api_key",
    "doctor",
    "get_config",
    "list_models",
    "process_prompt",
    "set_model",
]
