"""Small-model editing prepared by ProtoLink's native recoverable replacement tool."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from protolink import Artifact, Part
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


def process_tool_with_identity(native):
    """Retain executable spelling needed by venv Python and rustup proxy binaries.

    ProtoLink validates and executes the process. This adapter only prepares the
    logical absolute executable and rebuilds its preview before authorization.
    """

    def prepare(arguments, context):
        action = native.action_builder(arguments, context)
        original = arguments["argv"][0]
        if not Path(original).is_absolute():
            original = (
                str(Path(action.payload["arguments"]["cwd"]) / original)
                if "/" in original
                else shutil.which(original, path=arguments["env"].get("PATH", ""))
            )
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
        native.func,
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
        target = safe_path(arguments["path"], workspace)
        data = target.read_bytes()
        if hashlib.sha256(data).hexdigest() != arguments["expected_revision"]:
            raise ValueError("Source revision changed; read the file again before editing")
        content = data.decode("utf-8")
        old = arguments["old"]
        if not old or content.count(old) != 1:
            raise ValueError("old must match exactly once; include more surrounding source")
        action = native_replace.action_builder(
            {"path": str(target), "content": content.replace(old, arguments["new"], 1)}, context
        )
        action.metadata["editing_tool"] = "edit_file"
        return action

    return NativePreparedAdapter(
        edit_file,
        native=native_replace,
        generated_key="content",
        prepare=prepare,
        execute=native_replace.execute_authorized,
        capabilities=("filesystem.write",),
    )


def check_tool(native_process, record):
    def run_check(check_id: str, phase: str = "verify") -> dict:
        """Execute a frozen repository check by ID; baseline allows edits, verify closes this attempt's edit phase."""
        raise AssertionError("Signature only")

    def prepare(arguments, context):
        check_id, phase = arguments["check_id"], arguments.get("phase", "verify")
        if phase not in {"baseline", "verify"} or check_id not in record.selected:
            raise ValueError("Select a required check ID and phase baseline or verify")
        action = native_process.action_builder(record.checks[check_id].arguments(), context)
        action.metadata.update(check_id=check_id, check_phase=phase)
        return action

    return NativePreparedAdapter(
        run_check,
        native=native_process,
        generated_key="argv",
        prepare=prepare,
        execute=native_process.execute_authorized,
        capabilities=("process.execute",),
    )
