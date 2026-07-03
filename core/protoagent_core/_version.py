"""Version metadata for ProtoAgent components."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

__version__ = "0.1.0"

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CLI_VERSION_FALLBACK = "0.1.0"
_CLI_MANIFEST = _REPO_ROOT / "cli" / "Cargo.toml"
_ACP_VERSION_FILE = _REPO_ROOT / "acp" / "VERSION"
_ACP_VERSION_FALLBACK = "0.0.0-dev.0"


def _read_version_file(path: Path, fallback: str) -> str:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return fallback
    return value or fallback


def acp_version() -> str:
    """Return the planned ACP component version marker."""
    return _read_version_file(_ACP_VERSION_FILE, _ACP_VERSION_FALLBACK)


def cli_version() -> str:
    """Return the CLI package version from Cargo metadata when available."""
    try:
        data = tomllib.loads(_CLI_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return _CLI_VERSION_FALLBACK
    value = data.get("package", {}).get("version")
    return str(value) if value else _CLI_VERSION_FALLBACK


def component_versions(cli_version_override: str | None = None) -> dict[str, Any]:
    """Return the component version inventory used by docs and frontends."""
    return {
        "schema_version": 1,
        "components": [
            {
                "id": "cli",
                "name": "proto-cli",
                "version": cli_version_override or cli_version(),
                "status": "active",
                "source": "cli/Cargo.toml",
            },
            {
                "id": "core",
                "name": "protoagent-core",
                "version": __version__,
                "status": "active",
                "source": "core/pyproject.toml",
            },
            {
                "id": "acp",
                "name": "proto-acp",
                "version": acp_version(),
                "status": "planned",
                "source": "acp/VERSION",
            },
        ],
    }
