"""Select an external Python executable even when CPython is embedded in the CLI."""

from __future__ import annotations

import os
import re
import shutil
import sys
import sysconfig
from pathlib import Path


def python_executable(workspace: str | Path | None = None) -> str:
    """Prefer project Python, then runtime Python, without launching discovery probes.

    Preserve venv symlink spelling: resolving it can select the base environment.
    In PyO3, sys.executable can name proto-cli rather than a Python interpreter.
    Do not use that host binary as a subprocess or inherit its command directory.
    """
    if workspace is not None:
        root = Path(workspace).absolute()
        for relative in (".venv/bin/python", ".venv/Scripts/python.exe"):
            candidate = root / relative
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)

    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    names = (f"python{version}", "python3", "python.exe")
    # A regular Python process should retain its active environment.
    candidates = [sys.executable, getattr(sys, "_base_executable", "")]
    bindir = sysconfig.get_config_var("BINDIR")
    for directory in (Path(sys.prefix) / "bin", bindir, Path(sys.base_prefix) / "bin"):
        if directory:
            candidates.extend(str(Path(directory) / name) for name in names)
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).absolute()
        if (
            re.fullmatch(r"python(?:\d+(?:\.\d+)*)?(?:\.exe)?", path.name, re.IGNORECASE)
            and path.is_file()
            and os.access(path, os.X_OK)
        ):
            return str(path)
    for name in names:
        if executable := shutil.which(name):
            return str(Path(executable).absolute())
    raise RuntimeError("No external Python interpreter found; configure a project .venv")
