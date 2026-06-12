"""Coder agent factory."""

from __future__ import annotations

from typing import Any

from .. import tools
from .common import (
    QUIET_LOGGER,
    conversation_storage,
    create_selected_llm,
    record_side_effect,
    resolve_agent_url,
    set_transport_timeout,
    session_aware_agent_class,
    with_workspace_contract,
)

CODER_SYSTEM_PROMPT = """You are the ProtoAgent Coder.

Given a user objective and Explorer context, produce exact file modifications.
Use your tools to generate unified diffs or new-file proposals. Do not write to
disk. Return pending actions that require frontend approval.

Before producing a diff, make sure you have enough original content or context
from Explorer. Keep changes focused and explain assumptions briefly.
When the user asks to create a file, call create_new_file. Do not merely return
code for the user to copy. If a tiny script has no explicit path, choose a
conservative project-relative path such as scripts/<descriptive-name>.py and
state that assumption in the final response.
"""


def create_coder_agent(
    registry=None,
    provider: str = "ollama",
    model: str | None = None,
    workspace: str | None = None,
    url: str | None = None,
    transport: str = "sse",
    side_effects: list[dict[str, Any]] | None = None,
):
    """Create the diff synthesis agent."""
    Agent = session_aware_agent_class()

    agent = Agent(
        card={
            "name": "coder",
            "description": (
                "Diff synthesis agent. Generates unified diffs and new-file "
                "proposals without writing to disk."
            ),
            "url": resolve_agent_url("coder", url),
            "capabilities": {
                "delegation": False,
                "tool_calling": True,
                "multi_step_reasoning": True,
            },
            "tags": ["protoagent", "diffs", "coding"],
        },
        transport=transport,
        registry=registry,
        llm=create_selected_llm(provider, model),
        system_prompt=with_workspace_contract(CODER_SYSTEM_PROMPT, workspace, "Coder"),
        storage=conversation_storage("coder"),
        state=["conversation"],
        logger=QUIET_LOGGER,
        verbosity=0,
    )
    set_transport_timeout(agent.transport, 600)

    @agent.tool(
        name="generate_unified_diff",
        description="Generate a unified diff for replacing a file with updated content.",
        input_schema={"path": str, "updated_content": str, "original_content": str},
    )
    def generate_unified_diff(
        path: str,
        updated_content: str,
        original_content: str | None = None,
    ) -> dict[str, Any]:
        result = tools.generate_unified_diff(path, updated_content, original_content, workspace)
        record_side_effect(side_effects, {"source": "coder", **result})
        return result

    @agent.tool(
        name="create_new_file",
        description="Prepare a new file creation as a pending approval action.",
        input_schema={"path": str, "content": str},
    )
    def create_new_file(path: str, content: str) -> dict[str, Any]:
        result = tools.create_new_file(path, content, workspace)
        record_side_effect(side_effects, {"source": "coder", **result})
        return result

    return agent
