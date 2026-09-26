"""Coder agent factory."""

from __future__ import annotations

from protolink import Agent
from protolink.tools.builtins import filesystem_tools
from protolink.transport import Transport
from protolink.types import TransportType

from .. import tools
from ..checkpoints import checkpoint_store
from ..editing import edit_tool
from ..runtime_policy import WorkspacePolicy
from ..task_record import add_task_tools
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

Read the assigned source using read_file(path, start_line, end_line). Use edit_file
with one exact old/new replacement and the returned revision. Prefer it over
regenerating a file. Use create_file for new files and replace_file only for
small complete replacements. Follow task_status criteria and file scope. Paths
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
    single_agent: bool = False,
):
    """Create the stateless policy-gated file modification worker.

    ``tool_only`` skips model construction for deterministic CLI recovery.
    ProtoLink owns previews, revision checks, checkpoints and atomic mutation.
    """
    record = attempt.record if attempt is not None else None
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
                (
                    "You are a single coding agent. Read relevant source and tests, "
                    "define criteria, apply focused edits and run every required "
                    "repository check with run_check. Baselines permit later editing; "
                    "final verification closes editing until a bounded repair. "
                    "Use plan_task and task_status for criteria and scope. "
                    "Never claim completion without native writes and final checks. "
                    "Use exact edit_file replacements with a read_file revision; "
                    "native recovery and approvals apply to every write."
                    if single_agent
                    else CODER_SYSTEM_PROMPT
                ),
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
                "workspace.read": "allow",
                "task.manage": "allow",
                "filesystem.write": "require_approval",
                "filesystem.restore": "require_approval",
                **({"process.execute": "require_approval"} if single_agent else {}),
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
    agent.add_tool(edit_tool(agent.tools["replace_file"], workspace))

    @agent.tool(capabilities=["workspace.read"])
    def read_file(path: str, start_line: int = 1, end_line: int | None = None) -> dict:
        """Read a bounded assigned source span and its revision before editing."""
        absolute = str(tools.safe_path(path, workspace))
        result = tools.read_file(
            path,
            workspace,
            with_line_numbers=False,
            start_line=start_line,
            end_line=end_line,
            max_chars=4000 if prompt_profile == "small" else 8192,
        )
        if record and result.get("success"):
            record.source_paths.add(absolute)
        return result

    add_task_tools(agent, record, "coder")
    return agent
