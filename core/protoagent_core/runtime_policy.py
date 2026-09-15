"""Application authorization and bounded edit/check phases over native policies."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from protolink import (
    ApprovalScope,
    CapabilityPolicy,
    PolicyDecision,
    PolicyEffect,
    ResourceRevision,
    RunContext,
    StorageCheckpointStore,
)
from protolink.tools.builtins.filesystem import FilesystemResource

from . import config


@dataclass
class RunAuthorization:
    """Trusted authorization for one owned, authenticated agent mesh.

    Delegated contexts inherit the application's trace and workspace. Only the
    application's policy admits their run IDs; UI JSON cannot enlarge the scope.
    """

    context: RunContext
    run_ids: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.run_ids.add(self.context.run_id)

    @property
    def scope(self) -> ApprovalScope:
        return ApprovalScope(frozenset(self.run_ids))

    def admit(self, context: RunContext) -> bool:
        """Admit descendants of this mesh's trusted root context, not another run."""
        if (
            context.trace_id != self.context.trace_id
            or context.workspace_uri != self.context.workspace_uri
            or (context.run_id != self.context.run_id and not context.parent_run_id)
        ):
            return False
        self.run_ids.add(context.run_id)
        return True


@dataclass
class AttemptState:
    """App workflow phases and revision dependencies; no process or file executor."""

    workspace: str
    checkpoints: StorageCheckpointStore
    authorization: RunAuthorization
    attempt: int = 0
    checking: bool = False
    command_revisions: dict[str, tuple[ResourceRevision, ...]] = field(default_factory=dict)
    command_attempts: dict[str, int] = field(default_factory=dict)
    denied: bool = False

    def begin(self) -> None:
        """Start an edit phase only when dispatched by the bounded native Graph."""
        self.attempt += 1
        self.checking = False

    def file_changes(self):
        """Read this application's admitted run changes through native paginated inventory."""
        for run_id in sorted(self.authorization.run_ids):
            offset = 0
            while page := self.checkpoints.list_changes(run_id=run_id, limit=100, offset=offset):
                yield from page
                offset += len(page)

    def has_uncertain_changes(self) -> bool:
        """Stop work when native inventory records an unresolved effect in this run."""
        return any(
            self.checkpoints.list_changes(run_id=run_id, state=state, limit=1)
            for run_id in self.authorization.run_ids
            for state in ("prepared", "restoring", "uncertain")
        )

    def prepare_check(self, action_id: str) -> None:
        """Freeze edits and bind checks to the native revisions of this run's files."""
        self.checking = True
        resources = FilesystemResource([self.workspace])
        paths = {item.before.revision.resource_id for item in self.file_changes()}
        self.command_revisions[action_id] = tuple(
            resources.read(path).revision for path in sorted(paths)
        )
        self.command_attempts[action_id] = self.attempt


class WorkspacePolicy(CapabilityPolicy):
    """Constrain prepared actions without changing their arguments or artifacts."""

    def __init__(self, rules, *, workspace, authorization=None, attempt=None):
        super().__init__(rules, default_effect="deny", name="protoagent_workspace")
        self.workspace = Path(workspace).resolve()
        self.authorization = authorization
        self.attempt = attempt

    def deny(self, reason: str) -> PolicyDecision:
        if self.attempt is not None:
            self.attempt.denied = True
        return PolicyDecision(PolicyEffect.DENY, reason, "protoagent_workspace")

    async def evaluate(self, action, context):
        if self.authorization is not None and not self.authorization.admit(context):
            return self.deny("Action does not belong to the authorized application run")
        decision = await super().evaluate(action, context)
        if decision.effect.value == "deny":
            if self.attempt is not None:
                self.attempt.denied = True
            return decision
        if self.attempt is not None and action.capabilities.intersection(
            {"filesystem.write", "filesystem.restore", "process.execute"}
        ):
            if self.attempt.has_uncertain_changes():
                return self.deny(
                    "An earlier file effect is uncertain; inspect it before requesting new work"
                )
        args = action.payload.get("arguments", {})
        if "process.execute" in action.capabilities:
            cwd = Path(args["cwd"]).resolve()
            if not cwd.is_relative_to(self.workspace):
                return self.deny("Command working directory must remain inside the project")
            if self.attempt is not None:
                self.attempt.prepare_check(action.action_id)
        if "filesystem.write" in action.capabilities or "filesystem.restore" in action.capabilities:
            recovery = action.payload.get("recovery", {})
            before = recovery.get("before", recovery.get("change", {}).get("before", {}))
            path = args.get("path") or before.get("revision", {}).get("resource_id")
            if path and Path(path).resolve().is_relative_to(config.CONFIG_DIR.resolve()):
                return self.deny("The application's private storage is not a Coder target")
            if self.attempt is not None and self.attempt.checking:
                return self.deny(
                    "This attempt is already checking. Return the measured result; "
                    "only the bounded workflow may start a new repair attempt."
                )
        return decision
