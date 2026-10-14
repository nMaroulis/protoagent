"""Coder agent factory."""

from __future__ import annotations

from protolink.tools.builtins import filesystem_tools
from protolink.transport import Transport
from protolink.types import TransportType

from .. import tools
from ..checkpoints import checkpoint_store
from ..editing import edit_tool, workspace_file_tool
from ..runtime_policy import WorkspacePolicy
from ..task_record import add_task_tools
from ..tools import workspace_root
from ..worker_budget import BoundedCoderAgent, coder_budget_hooks
from .common import (
    QUIET_LOGGER,
    create_configured_transport,
    create_selected_llm,
    resolve_agent_url,
    with_prompt_profile,
    with_workspace_contract,
)

CODER_SYSTEM_PROMPT = """You are the ProtoAgent Coder, a stateless file worker.

Your job ends after the requested edits are applied. Return a concise final
outcome to Architect then. Architect owns baseline and final verification;
run_check and execute_command are not your tools. Check IDs in the task record
describe required regression coverage, not commands you should run. Do not keep
exploring after the requested edits or promise to run checks yourself.

Read the assigned source using read_file(path, start_line, end_line). Use edit_file
with one exact old/new replacement and read_file.revision. A successful edit
returns source_revision for the next edit of source you already know; use that
instead of another read. Native resource.version is a recovery identity, not
expected_revision. Reread only for missing source or a source rejection.
Prefer edit_file over
regenerating a file. Copy old literally from read_file.content, including spaces
and line breaks. Use a short unique span, such as one import or one function;
never reconstruct the old file or invent placeholder source. old and new must
differ; an identical replacement is not progress. Keep replacements small to
avoid malformed JSON. If a read is
truncated, use next_line to read more before changing unseen code. After a
stale revision or selection rejection, reread and correct it within the native
allowance. For introduced_python_syntax, no write occurred: correct the proposed
new/content using the source and revision you already observed. JSON string
newlines must decode to real line breaks; never double-escape them into literal
backslash-n text. The parse-only guard does not check imports or runtime behavior.
Do not retry denied, interrupted or uncertain writes.
Use create_file for new files and replace_file only for
small complete replacements. Follow task_status criteria and file scope. Paths
may be project-relative or absolute beneath the project root; parent directories must already
exist. The native tools reject symlinks. Ask Architect for an explicitly approved
command if a missing directory must be created before a later edit attempt.
For bootstrap_checks, create focused stdlib unittest regressions in root
test_*.py files within the assigned scope. Exercise the requested behavior,
not just imports or syntax. Verifier runs them; you never claim they passed.

Each write prepares an exact unified diff for native authorization, checks the
preimage and saves recovery bytes before changing the file. The user's project
approval preference may resolve writes automatically. A preview or approval is not an
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
    agent = BoundedCoderAgent(
        work_record=record,
        work_attempt=attempt,
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
                    "Use ask_user to clarify missing requirements; feedback never grants approval. "
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
                **({"user.interact": "allow"} if single_agent else {}),
            },
            workspace=workspace_root(workspace),
            authorization=authorization,
            attempt=attempt,
        ),
        approval_handler=approval_handler,
        verbosity=0,
    )

    native_files = filesystem_tools(
        roots=[workspace_root(workspace)],
        checkpoints=checkpoints if checkpoints is not None else checkpoint_store(workspace),
    )
    for tool in native_files:
        agent.add_tool(
            workspace_file_tool(tool, workspace)
            if tool.name in {"create_file", "replace_file"}
            else tool
        )
    agent.execution_version += ":protoagent-file-paths-2"
    # Exact edits prepare the raw native replacement once. The full-file
    # interface has its own validation adapter and need not be nested here.
    agent.add_tool(
        edit_tool(next(tool for tool in native_files if tool.name == "replace_file"), workspace)
    )
    agent.execution_version += ":protoagent-source-feedback-3"
    if not single_agent and not tool_only:
        # Context preparation combines this hook with schema admission so the
        # stop boundary survives rebuilding. Standalone agents use the
        # same public lifecycle extension without context preparation.
        budget_hooks = coder_budget_hooks(record)
        setattr(agent, "protoagent_coder_budget_hooks", budget_hooks)
        agent.hooks = (*agent.hooks, budget_hooks)
        agent.execution_version += ":protoagent-coder-budget-3"

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
