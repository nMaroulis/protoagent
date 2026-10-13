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
    candidates = [sys.executable]
    active = os.getenv("VIRTUAL_ENV")
    if active:
        candidates.extend(str(Path(active) / name) for name in ("bin/python", "Scripts/python.exe"))
    if sys.prefix != sys.base_prefix:
        candidates.extend(str(Path(sys.prefix) / "bin" / name) for name in names)
    # The embedded CLI adds its dependency environment to sys.path without
    # changing CPython's base prefix. Retain that environment for subprocesses,
    # instead of selecting the bare framework Python which lacks its packages.
    for entry in sys.path:
        path = Path(entry)
        if path.name != "site-packages":
            continue
        for root in path.parents:
            if (root / "pyvenv.cfg").is_file():
                candidates.extend(str(root / name) for name in ("bin/python", "Scripts/python.exe"))
                break
    candidates.append(getattr(sys, "_base_executable", ""))
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
