"""Coder agent factory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from protolink import Agent, Artifact, CapabilityPolicy, Part, RunAction, RunContext

from .. import tools
from .common import (
    QUIET_LOGGER,
    conversation_storage,
    create_selected_llm,
    resolve_agent_url,
    set_transport_timeout,
    with_workspace_contract,
)

CODER_SYSTEM_PROMPT = """You are the ProtoAgent Coder.

Given a user objective and Explorer context, produce exact file modifications.
Use your tools for file changes. Each tool prepares a unified-diff preview and
Protolink pauses it for application approval before the tool writes to disk.

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
    approval_handler=None,
):
    """Create the policy-gated file modification agent."""
    agent = Agent(
        card={
            "name": "coder",
            "description": (
                "File modification agent. Previews writes as unified diffs and "
                "executes them only after runtime authorization."
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
        policy=CapabilityPolicy({"workspace.write": "require_approval"}),
        approval_handler=approval_handler,
        verbosity=0,
    )
    set_transport_timeout(agent.transport, 600)

    @agent.tool(
        name="generate_unified_diff",
        description="Replace a file after presenting a unified diff for approval.",
        capabilities=["workspace.write"],
        action_builder=lambda arguments, context: _build_write_action(
            arguments,
            context,
            workspace,
            create=False,
        ),
    )
    def generate_unified_diff(
        path: str,
        updated_content: str,
        original_content: str | None = None,
    ) -> dict[str, Any]:
        return tools.write_file(path, updated_content, workspace)

    @agent.tool(
        name="create_new_file",
        description="Create a new file after presenting a unified diff for approval.",
        capabilities=["workspace.write"],
        action_builder=lambda arguments, context: _build_write_action(
            arguments,
            context,
            workspace,
            create=True,
        ),
    )
    def create_new_file(path: str, content: str) -> dict[str, Any]:
        return tools.write_file(path, content, workspace, overwrite=False)

    return agent


def _build_write_action(
    arguments: dict[str, Any],
    context: RunContext,
    workspace: str | None,
    *,
    create: bool,
) -> RunAction:
    """Prepare a workspace write with a structured unified-diff preview."""
    path = str(arguments["path"])
    content_key = "content" if create else "updated_content"
    content = str(arguments[content_key])
    preview = (
        tools.create_new_file(path, content, workspace)
        if create
        else tools.generate_unified_diff(path, content, original_content=None, workspace=workspace)
    )
    if not preview.get("success"):
        raise ValueError(str(preview.get("error") or f"Could not prepare {path}"))

    target = tools.safe_path(path, workspace)
    artifact = Artifact(
        kind="preview",
        name=str(preview["path"]),
        uri=Path(target).as_uri(),
        media_type="text/x-diff",
        parts=[Part.text(str(preview.get("diff", "")))],
        metadata={"path": str(preview["path"]), "purpose": "approval_preview"},
    )
    action = RunAction(
        kind="workspace.create" if create else "workspace.write",
        name="create_new_file" if create else "replace_file",
        payload={"arguments": dict(arguments)},
        description=("Create" if create else "Replace") + f" {preview['path']}",
        metadata={"path": str(preview["path"]), "workspace_uri": context.workspace_uri},
    )
    return action.with_artifacts([artifact])
