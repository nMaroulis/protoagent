"""Small-model editing prepared by ProtoLink's native recoverable replacement tool."""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import replace
from pathlib import Path

from protolink import Artifact, Part, RunAction, ToolRejectedError
from protolink.tools.prepared import PreparedTool

from .tools import safe_path


class NativePreparedAdapter(PreparedTool):
    """Validate model input and the expanded native operation with their own schemas.

    ProtoLink validates both sides of preparation. Native fields are produced by
    the builder; supplying those fields directly cannot execute because the
    builder still requires the original model arguments.
    """

    def __init__(self, *args, native, generated_key, **kwargs):
        super().__init__(*args, **kwargs)
        self.native = native
        self.generated_key = generated_key

    def validate_args(self, kwargs):
        if kwargs and self.generated_key in kwargs:
            return self.native.validate_args(kwargs)
        return super().validate_args(kwargs)


def process_tool_with_identity(native, workspace=None):
    """Resolve project commands before native preparation and exact approval.

    Bare names use explicit env.PATH or a fixed application search path; the
    child still receives exactly its specified environment. Relative cwd belongs
    to the project, never the CLI's launch directory. Preserve venv spelling.
    """
    from .python_runtime import python_executable
    from .tools import workspace_root

    root = Path(workspace_root(workspace))
    search_path = os.pathsep.join(("/opt/homebrew/bin", "/usr/local/bin", os.defpath))

    def execute_command(
        argv: list[str],
        cwd: str = ".",
        env: dict[str, str] | None = None,
        timeout_seconds: float = 60.0,
        max_output_bytes: int = 8192,
    ) -> dict:
        """Run approved argv without a shell. cwd is project-relative; env defaults to empty. Bare programs resolve before approval; python/python3 prefer project Python when PATH is absent. No environment is inherited."""
        raise AssertionError("Signature only")

    def prepare(arguments, context):
        args = dict(arguments)
        directory = Path(args.get("cwd", "."))
        args["cwd"] = str(directory if directory.is_absolute() else root / directory)
        args["env"] = dict(args.get("env") or {})
        args.setdefault("timeout_seconds", 60.0)
        args.setdefault("max_output_bytes", 8192)
        argv = list(args["argv"])
        if argv and argv[0] and not Path(argv[0]).is_absolute():
            if os.path.dirname(argv[0]):
                argv[0] = str(Path(args["cwd"]) / argv[0])
            elif argv[0] in {"python", "python3"} and "PATH" not in args["env"]:
                argv[0] = python_executable(root)
            else:
                lookup = os.pathsep.join(
                    str(Path(args["cwd"]) / entry) if not Path(entry).is_absolute() else entry
                    for entry in args["env"].get("PATH", search_path).split(os.pathsep)
                )
                resolved = shutil.which(argv[0], path=lookup)
                if not resolved:
                    raise ValueError(
                        f"Executable {argv[0]!r} was not found; use an installed absolute path or explicit env.PATH"
                    )
                argv[0] = resolved
        args["argv"] = argv
        action = native.action_builder(args, context)
        original = argv[0]
        action.payload["arguments"]["argv"][0] = str(Path(original).absolute())
        return action.with_artifacts(
            [
                Artifact(
                    kind="preview",
                    name="Command execution",
                    parts=[
                        Part.json(
                            {
                                **action.payload["arguments"],
                                "boundary": action.payload["boundary"],
                                "implicit_shell": False,
                            }
                        )
                    ],
                )
            ]
        )

    return PreparedTool(
        execute_command,
        prepare=prepare,
        execute=native.execute_authorized,
        capabilities=("process.execute",),
        name="execute_command",
    )


def edit_tool(native_replace, workspace):
    def edit_file(path: str, old: str, new: str, expected_revision: str) -> dict:
        """Replace one exact occurrence; use the revision returned by read_file. No regex or fuzzy matching."""
        raise AssertionError("Signature only")

    def prepare(arguments, context):
        def reject(message, code, details, suggestion):
            # ProtoLink 0.8.1 corrects ToolRejectedError from execution, but
            # preparation errors terminate inference. Prepare only read-only
            # feedback here; the native loop owns its correction allowance.
            return RunAction(
                kind="tool.validation",
                name="edit_file",
                payload={
                    "arguments": arguments,
                    "source_rejection": ToolRejectedError(
                        message, code=code, details=details, suggestion=suggestion
                    ).to_dict(),
                },
                capabilities=frozenset({"workspace.read"}),
                description="Report an invalid source selection; no write was prepared.",
            )

        target = safe_path(arguments["path"], workspace)
        data = target.read_bytes()
        if hashlib.sha256(data).hexdigest() != arguments["expected_revision"]:
            return reject(
                "Source revision changed; no edit was prepared",
                "stale_source_revision",
                {"path": str(target)},
                "Read the current source and use its returned revision in a new edit request.",
            )
        content = data.decode("utf-8")
        old = arguments["old"]
        if not old or content.count(old) != 1:
            return reject(
                "old must match exactly once; no edit was prepared",
                "ambiguous_source_match",
                {"path": str(target), "matches": content.count(old) if old else 0},
                "Reread a small source span. Copy old exactly from content, including whitespace; use a short unique span. Do not reconstruct the file or invent placeholders.",
            )
        action = native_replace.action_builder(
            {"path": str(target), "content": content.replace(old, arguments["new"], 1)}, context
        )
        action.metadata["editing_tool"] = "edit_file"
        return action

    async def execute(execution):
        execution.check()
        action = execution.authorization.action
        if action.kind == "tool.validation":
            raise ToolRejectedError.from_dict(action.payload["source_rejection"])
        return await native_replace.execute_authorized(execution)

    return NativePreparedAdapter(
        edit_file,
        native=native_replace,
        generated_key="content",
        prepare=prepare,
        execute=execute,
        # Validation reads source. A valid proposal uses the native replacement
        # action, which adds filesystem.write before policy and exact approval.
        capabilities=("workspace.read",),
    )


def workspace_file_tool(native, workspace):
    """Expand a project-relative model path; native validation and execution stay intact.

    Join without resolving: the native builder must still see and reject parent
    traversal, symlink targets and paths outside its original allowed roots.
    """
    from .tools import workspace_root

    root = Path(workspace_root(workspace))

    def prepare(arguments, context):
        path = Path(arguments["path"])
        if not path.is_absolute():
            path = root / path
        return native.action_builder({**arguments, "path": str(path)}, context)

    tool = PreparedTool(
        native.func,
        name=native.name,
        prepare=prepare,
        execute=native.execute_authorized,
        capabilities=tuple(native.capabilities),
    )
    tool.description = "Use a project-relative or absolute path. " + native.description
    return tool


def check_tool(native_process, record):
    def run_check(check_id: str, phase: str = "verify") -> dict:
        """Execute a frozen repository check by ID; baseline allows edits, verify closes this attempt's edit phase."""
        raise AssertionError("Signature only")

    def prepare(arguments, context):
        check_id, phase = arguments["check_id"], arguments.get("phase", "verify")
        if phase not in {"baseline", "verify"} or check_id not in record.selected:
            raise ToolRejectedError(
                "Select a required check ID and phase baseline or verify",
                code="invalid_check_request",
                details={
                    "required_checks": list(record.selected),
                    "phases": ["baseline", "verify"],
                },
                suggestion="Use a required check ID; arbitrary commands cannot satisfy verification.",
            )
        if phase == "baseline" and check_id not in record.baseline_check_ids():
            from .run_contracts import infer_run_contract

            contract = infer_run_contract(record.objective)
            if contract.requires_write and not contract.allows_unverified_docs:
                raise ToolRejectedError(
                    "No existing tests are available for this bootstrap baseline; no command was prepared",
                    code="baseline_unavailable",
                    details={
                        "check_id": check_id,
                        "baseline_check_ids": record.baseline_check_ids(),
                    },
                    suggestion="Inspect the requested source, ask Coder to apply the change and add focused root test_*.py regressions, then run phase=verify. A baseline is possible after regression files exist.",
                )
        action = native_process.action_builder(record.checks[check_id].arguments(), context)
        action.metadata.update(check_id=check_id, check_phase=phase)
        return replace(
            action,
            description=(
                f"Run existing baseline check {check_id} before editing."
                if phase == "baseline"
                else f"Run verification check {check_id} against the current files."
            ),
        )

    return NativePreparedAdapter(
        run_check,
        native=native_process,
        generated_key="argv",
        prepare=prepare,
        execute=native_process.execute_authorized,
        capabilities=("process.execute",),
    )
