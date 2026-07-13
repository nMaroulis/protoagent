"""Coder agent factory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from protolink import Agent, Artifact, CapabilityPolicy, Part, RunAction, RunContext
from protolink.transport import Transport
from protolink.types import TransportType

from .. import tools
from .common import (
    QUIET_LOGGER,
    create_configured_transport,
    create_selected_llm,
    resolve_agent_url,
    with_prompt_profile,
    with_workspace_contract,
)

CODER_SYSTEM_PROMPT = """You are the ProtoAgent Coder.

You are a stateless, task-local write worker. Do not rely on prior conversation
memory; use only the objective, Explorer context, and your write-preview tools.

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
    transport: TransportType | Transport | None = "sse",
    approval_handler=None,
    telemetry=None,
    prompt_profile: str = "auto",
    authenticator=None,
    credentials: str | None = None,
):
    """Create the stateless policy-gated file modification worker."""
    agent_url = resolve_agent_url("coder", url)
    agent = Agent(
        card={
            "name": "coder",
            "description": (
                "Stateless file modification worker. Previews writes as "
                "unified diffs and executes them only after runtime authorization."
            ),
            "url": agent_url,
            "capabilities": {
                "delegation": False,
                "tool_calling": True,
                "multi_step_reasoning": True,
            },
            "tags": ["protoagent", "diffs", "coding"],
        },
        transport=create_configured_transport(
            transport,
            agent_url,
            authenticator=authenticator,
            credentials=credentials,
        ),
        registry=registry,
        llm=create_selected_llm(provider, model),
        system_prompt=with_workspace_contract(
            with_prompt_profile(
                CODER_SYSTEM_PROMPT,
                "coder",
                provider,
                model,
                prompt_profile,
            ),
            workspace,
            "Coder",
        ),
        storage=None,
        state=[],
        telemetry=telemetry,
        logger=QUIET_LOGGER,
        authenticator=authenticator,
        credentials=credentials,
        policy=CapabilityPolicy(
            {
                "workspace.write": "require_approval",
            },
            default_effect="deny",
        ),
        approval_handler=approval_handler,
        verbosity=0,
    )

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
