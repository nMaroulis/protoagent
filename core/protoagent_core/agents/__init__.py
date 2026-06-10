"""ProtoAgent agent factories."""

from .architect import ARCHITECT_SYSTEM_PROMPT, create_architect_agent
from .coder import CODER_SYSTEM_PROMPT, create_coder_agent
from .deck import agent_manifest, create_agent_deck
from .explorer import EXPLORER_SYSTEM_PROMPT, create_explorer_agent

__all__ = [
    "ARCHITECT_SYSTEM_PROMPT",
    "CODER_SYSTEM_PROMPT",
    "EXPLORER_SYSTEM_PROMPT",
    "agent_manifest",
    "create_agent_deck",
    "create_architect_agent",
    "create_coder_agent",
    "create_explorer_agent",
]

