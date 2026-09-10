"""Coder agent factory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from protolink import Agent, Artifact, CapabilityPolicy, Part, RunAction, RunContext
from protolink.transport import Transport
from protolink.types import TransportType

from .. import checkpoints, tools
from ..verification import VerificationEvidence
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
ProtoLink pauses it for application approval before the tool writes to disk.

Before producing a diff, make sure you have enough original content or context
from Explorer. Keep changes focused and explain assumptions briefly.
When the user asks to create a file, call create_new_file. Do not merely return
code for the user to copy. If a tiny script has no explicit path, choose a
conservative project-relative path such as scripts/<descriptive-name>.py and
state that assumption in the final response.
Applied changes return checkpoint IDs. Restore an earlier Coder change with
restore_checkpoint only when the task calls for undo; it requires a fresh
approval and rejects files changed after that write. expected_hash is internal
preview data filled by the action builder; omit it from model tool calls.
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
    evidence: VerificationEvidence | None = None,
    tool_only: bool = False,
):
    """Create the stateless policy-gated file modification worker.

    ``tool_only`` skips model construction for deterministic CLI recovery.
    ``evidence`` invalidates prior checks only after an actual file mutation.
    """
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
        llm=None if tool_only else create_selected_llm(provider, model),
        expose_chat=not tool_only,
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
        expected_hash: str = "",
    ) -> dict[str, Any]:
        """Apply the approved preview, retaining its preimage for conflict-safe undo."""
        result = checkpoints.write_change(path, updated_content, expected_hash, workspace)
        if evidence and result.get("changed"):
            evidence.changed()
        return result

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
    def create_new_file(path: str, content: str, expected_hash: str = "") -> dict[str, Any]:
        """Create the authorized file and retain a checkpoint for removing it later."""
        result = checkpoints.write_change(path, content, expected_hash, workspace)
        if evidence and result.get("changed"):
            evidence.changed()
        return result

    @agent.tool(
        name="restore_checkpoint",
        description="Undo one earlier Coder file write after a fresh diff approval. Refuses files changed since that write.",
        capabilities=["workspace.write"],
        action_builder=lambda arguments, context: _build_restore_action(
            arguments, context, workspace
        ),
    )
    def restore_checkpoint(
        checkpoint_id: str = "latest", expected_hash: str = ""
    ) -> dict[str, Any]:
        """Restore only the exact checkpoint selected in the authorized action."""
        result = checkpoints.restore_checkpoint(checkpoint_id, expected_hash, workspace)
        if evidence:
            evidence.changed()
        return result

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
    target = tools.safe_path(path, workspace)
    before = checkpoints.file_content(target)
    preview = (
        tools.create_new_file(path, content, workspace)
        if create
        else tools.generate_unified_diff(
            path, content, original_content=(before or b"").decode("utf-8"), workspace=workspace
        )
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
        payload={
            "arguments": {
                **arguments,
                "path": str(target),
                "expected_hash": checkpoints.fingerprint(before),
            }
        },
        description=("Create" if create else "Replace") + f" {preview['path']}",
        metadata={"path": str(preview["path"]), "workspace_uri": context.workspace_uri},
    )
    return action.with_artifacts([artifact])


def _build_restore_action(
    arguments: dict[str, Any], context: RunContext, workspace: str | None
) -> RunAction:
    """Resolve latest before approval so the selected snapshot cannot drift."""
    preview = checkpoints.restore_preview(str(arguments.get("checkpoint_id", "latest")), workspace)
    return RunAction(
        kind="workspace.restore",
        name="restore_checkpoint",
        description=f"Undo agent change to {preview['path']}",
        payload={
            "arguments": {
                "checkpoint_id": preview["checkpoint_id"],
                "expected_hash": preview["expected_hash"],
            }
        },
        metadata={"path": preview["path"], "workspace_uri": context.workspace_uri},
    ).with_artifacts(
        [
            Artifact(
                kind="preview",
                name=preview["path"],
                media_type="text/x-diff",
                parts=[Part.text(preview["diff"])],
                metadata={"path": preview["path"], "purpose": "approval_preview"},
            )
        ]
    )
