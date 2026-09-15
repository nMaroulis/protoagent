"""Coder agent factory."""

from __future__ import annotations

from protolink import Agent
from protolink.tools.builtins import filesystem_tools
from protolink.transport import Transport
from protolink.types import TransportType

from ..checkpoints import checkpoint_store
from ..runtime_policy import WorkspacePolicy
from ..tools import workspace_root
from .common import (
    QUIET_LOGGER,
    create_configured_transport,
    create_selected_llm,
    resolve_agent_url,
    with_prompt_profile,
    with_workspace_contract,
)

CODER_SYSTEM_PROMPT = """You are the ProtoAgent Coder, a stateless file worker.

Use the current objective and Explorer's exact file context. Apply focused edits
with ProtoLink create_file(path, content) or replace_file(path, content). Paths
must be absolute and beneath the project root; parent directories must already
exist. The native tools reject symlinks. Ask Architect for an explicitly approved
command if a missing directory must be created before a later edit attempt.

Each write presents an exact unified diff for approval, checks the preimage and
saves recovery bytes before changing the file. A preview or approval is not an
applied edit. Report the native change_id and state returned after execution.
preview_change(change_id) inspects recovery records. restore_change(change_id)
requires separate approval and refuses changed resources. Use it only when the
user requests restoration. Surface conflicts and uncertain effects; never retry
an interrupted mutation or a denied action. Do not rely on conversation memory.
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
    checkpoints=None,
    authorization=None,
    attempt=None,
    tool_only: bool = False,
):
    """Create the stateless policy-gated file modification worker.

    ``tool_only`` skips model construction for deterministic CLI recovery.
    ProtoLink owns previews, revision checks, checkpoints and atomic mutation.
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
                "streaming": True,
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
        policy=WorkspacePolicy(
            {
                "filesystem.read": "allow",
                "filesystem.write": "require_approval",
                "filesystem.restore": "require_approval",
            },
            workspace=workspace_root(workspace),
            authorization=authorization,
            attempt=attempt,
        ),
        approval_handler=approval_handler,
        verbosity=0,
    )

    for tool in filesystem_tools(
        roots=[workspace_root(workspace)],
        checkpoints=checkpoints if checkpoints is not None else checkpoint_store(workspace),
    ):
        agent.add_tool(tool)
    return agent
