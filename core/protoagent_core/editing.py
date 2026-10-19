"""Small-model editing prepared by ProtoLink's native recoverable replacement tool."""

from __future__ import annotations

import ast
import hashlib
import os
import shutil
from dataclasses import replace
from pathlib import Path

from protolink import Artifact, Part, ToolRejectedError
from protolink.core.resources import ResourceSnapshot
from protolink.tools.prepared import PreparedTool

from .tools import safe_path


def _python_syntax_rejection(action):
    """Reject newly introduced Python syntax errors, without importing code.

    Existing malformed sources can still be repaired incrementally. This does
    not diagnose names, packages, types or behavior; those need real checks.
    """
    args = action.payload["arguments"]
    path = Path(args["path"])
    if path.suffix.lower() != ".py":
        return None
    try:
        ast.parse(args["content"], filename=str(path))
    except SyntaxError as error:
        before = ResourceSnapshot.from_dict(action.payload["recovery"]["before"])
        if before.data is not None:
            try:
                ast.parse(before.data.decode("utf-8"), filename=str(path))
            except (SyntaxError, UnicodeError):
                return None
        return ToolRejectedError(
            f"Proposed Python source has a syntax error on line {error.lineno}: {error.msg}. No file was changed.",
            code="introduced_python_syntax",
            details={"path": str(path), "line": error.lineno, "reason": error.msg},
            suggestion="Correct the proposed source. Newlines in JSON string arguments must decode to real line breaks, not literal backslash-n text. Do not double-escape them. No import or test was executed.",
        )
    return None


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
        try:
            return prepare_command(arguments, context)
        except (ValueError, OSError) as error:
            raise ToolRejectedError(
                str(error),
                code="invalid_command_preparation",
                suggestion="Use an existing project-relative cwd, an installed executable (or explicit env.PATH), timeout_seconds <= 600 and max_output_bytes <= 32768. No command was executed.",
            ) from error

    def prepare_command(arguments, context):
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
        """Replace one exact occurrence. Use read_file.revision or a successful edit_file.source_revision; resource.version is a different native recovery identity. No regex, fuzzy matching or identical old/new text."""
        raise AssertionError("Signature only")

    def prepare(arguments, context):
        def reject(message, code, details, suggestion):
            raise ToolRejectedError(message, code=code, details=details, suggestion=suggestion)

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
        if old == arguments["new"]:
            return reject(
                "old and new are identical; no edit was prepared",
                "unchanged_source",
                {"path": str(target)},
                "Choose an actual remaining change, or return your outcome to Architect if the assignment is finished. An unchanged replacement is not progress.",
            )
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
        if error := _python_syntax_rejection(action):
            raise error
        action.metadata["editing_tool"] = "edit_file"
        return action

    async def execute(execution):
        execution.check()
        action = execution.authorization.action
        result = await native_replace.execute_authorized(execution)
        if isinstance(result, dict) and result.get("state") == "applied":
            # Native resource.version includes filesystem identity. Exact edits
            # use a byte digest instead; return the approved afterimage's digest
            # so the worker need not reread known source after every small edit.
            # The next edit still checks the live bytes; native recovery and
            # resource identities are preserved.
            return {
                **result,
                "source_revision": hashlib.sha256(
                    action.payload["arguments"]["content"].encode("utf-8")
                ).hexdigest(),
            }
        return result

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
        action = native.action_builder({**arguments, "path": str(path)}, context)
        if error := _python_syntax_rejection(action):
            raise error
        return action

    async def execute(execution):
        execution.check()
        return await native.execute_authorized(execution)

    tool = PreparedTool(
        native.func,
        name=native.name,
        prepare=prepare,
        execute=execute,
        # Preparation rejection is handled by ProtoLink before approval or
        # tool-call accounting. Valid actions retain native write authority.
        capabilities=("workspace.read",),
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
        if phase == "verify" and record.reports.get("coder", {}).get("reason") in {
            "worker_turn_limit",
            "worker_no_progress",
        }:
            raise ToolRejectedError(
                "Coder returned unfinished work; no final check was prepared",
                code="coder_handoff_unresolved",
                details={"worker_report": record.reports["coder"]},
                suggestion="Inspect current source and assign only remaining edits to Coder. Resolve its partial handoff before final verification; never replay applied edits.",
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
