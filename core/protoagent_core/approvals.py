"""User-selected project approval preferences over native prepared actions."""

from __future__ import annotations

from pathlib import Path

from . import config
from .tools import workspace_root

MODES = {
    "ask": "Ask before file edits and commands.",
    "edits": "Auto-accept file edits; ask before commands.",
    "auto": "Auto-accept file edits and commands. Commands use normal host permissions.",
}


def approval_mode(workspace=None, settings=None):
    settings = config.load_config() if settings is None else settings
    modes = settings.get("approval_modes", {})
    value = modes.get(str(workspace_root(workspace)), "ask") if isinstance(modes, dict) else "ask"
    return value if isinstance(value, str) and value in MODES else "ask"


def approval_settings(workspace=None):
    root = str(workspace_root(workspace))
    mode = approval_mode(root)
    return {
        "mode": mode,
        "workspace": root,
        "description": MODES[mode],
        "modes": MODES,
        "always_ask": "MCP connections/calls and file restoration.",
        "scope": "Saved for this project; applies to subsequent runs.",
    }


def configure_approvals(value=None, workspace=None):
    if value is not None:
        mode = value.strip().lower()
        if mode not in MODES:
            raise ValueError("Approval mode must be ask, edits or auto")
        settings = config.load_config()
        modes = settings.get("approval_modes")
        if not isinstance(modes, dict):
            modes = settings["approval_modes"] = {}
        root = str(workspace_root(workspace))
        if mode == "ask":
            modes.pop(root, None)
        else:
            modes[root] = mode
        config.save_config(settings)
    return approval_settings(workspace)


def can_auto_accept(action, mode, workspace):
    """Select already-prepared native work; this cannot override a policy denial."""
    if mode not in {"edits", "auto"}:
        return False
    args = action.payload.get("arguments", {})
    root = Path(workspace).resolve()
    if (
        action.name in {"create_file", "replace_file"}
        and action.capabilities <= {"filesystem.write", "filesystem.read", "workspace.read"}
        and "filesystem.write" in action.capabilities
    ):
        path = args.get("path")
        return (
            isinstance(path, str)
            and Path(path).is_absolute()
            and Path(path).resolve().is_relative_to(root)
        )
    if (
        mode == "auto"
        and action.name == "execute_command"
        and action.capabilities == {"process.execute"}
    ):
        cwd = args.get("cwd")
        return (
            isinstance(cwd, str)
            and Path(cwd).is_absolute()
            and Path(cwd).resolve().is_relative_to(root)
        )
    return False
