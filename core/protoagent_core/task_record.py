"""Runtime-owned task packets and frozen, repository-backed verification plans."""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .tools import read_file, safe_path

PYTHON_BOOTSTRAP_SOURCE = "Python unittest bootstrap"


class PlanValidationError(ValueError):
    """Expected planning feedback; no plan fields change on rejection."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CheckSpec:
    id: str
    argv: tuple[str, ...]
    cwd: str
    env: dict[str, str]
    paths: tuple[str, ...] = ()
    source: str = "repository discovery"

    def arguments(self) -> dict[str, Any]:
        return dict(
            argv=list(self.argv),
            cwd=self.cwd,
            env=dict(self.env),
            timeout_seconds=120,
            max_output_bytes=8192,
        )

    def matches(self, arguments: dict[str, Any]) -> bool:
        argv = arguments.get("argv", [])
        return bool(argv) and (
            tuple([str(Path(argv[0]).absolute()), *argv[1:]]) == self.argv
            and str(Path(arguments.get("cwd", "")).resolve()) == self.cwd
            and arguments.get("env", {}) == self.env
        )


def discover_checks(workspace: str) -> dict[str, CheckSpec]:
    """Inspect manifests without importing project code or executing discovery."""
    root = Path(workspace).resolve()
    path_env: dict[str, str] = {
        "PATH": os.pathsep.join(
            dict.fromkeys(
                [
                    str(Path(sys.executable).parent),
                    "/opt/homebrew/bin",
                    "/usr/local/bin",
                    os.defpath,
                ]
            )
        )
    }
    checks: dict[str, CheckSpec] = {}
    manifest = root / ".protoagent" / "project.json"
    if manifest.is_symlink() or manifest.parent.is_symlink():
        raise ValueError("Project check configuration cannot use symlinks")
    manifest = safe_path(str(manifest), workspace)
    if manifest.exists():
        if manifest.is_symlink() or manifest.stat().st_size > 32768:
            raise ValueError("Project check configuration must be a bounded regular file")
        data = json.loads(manifest.read_text())
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("checks", []), list)
            or len(data.get("checks", [])) > 16
        ):
            raise ValueError("Project configuration requires at most 16 check specifications")
        for item in data.get("checks", []):
            check_id = str(item["id"])
            argv = item["argv"]
            if not check_id or check_id in checks or len(check_id) > 64:
                raise ValueError("Check IDs must be unique, nonempty and at most 64 characters")
            if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
                raise ValueError("Each check requires an argv string array")
            cwd = str(safe_path(item.get("cwd", "."), workspace))
            env = dict(item.get("env", {}))
            if not all(
                isinstance(key, str) and isinstance(value, str) for key, value in env.items()
            ):
                raise ValueError("Check environment must contain string keys and values")
            executable = argv[0]
            if not Path(executable).is_absolute():
                executable = (
                    str(safe_path(executable, cwd))
                    if "/" in executable
                    else shutil.which(executable, path=env.get("PATH", "")) or ""
                )
            if not executable:
                raise ValueError("Check executable requires an absolute path or explicit PATH")
            paths = tuple(str(safe_path(p, workspace)) for p in item.get("paths", []))
            checks[check_id] = CheckSpec(
                check_id,
                (str(Path(executable).absolute()), *argv[1:]),
                cwd,
                env,
                paths,
                ".protoagent/project.json",
            )
        return checks
    python = root / ".venv" / "bin" / "python"
    executable = str(python.absolute()) if python.exists() else sys.executable
    for directory in ("tests", "core/tests"):
        if (root / directory).is_dir():
            env = dict(path_env)
            if directory.startswith("core/"):
                env["PYTHONPATH"] = "core"
            checks["python-tests"] = CheckSpec(
                "python-tests",
                (executable, "-m", "unittest", "discover", "-s", directory, "-q"),
                str(root),
                env,
                (str(root / directory),),
            )
            break
    root_tests = tuple(str(path) for path in root.glob("test_*.py") if path.is_file())
    if "python-tests" not in checks and root_tests:
        checks["python-tests"] = CheckSpec(
            "python-tests",
            (executable, "-m", "unittest", "discover", "-s", ".", "-p", "test_*.py", "-q"),
            str(root),
            dict(path_env),
            root_tests,
        )
    if (root / "Cargo.toml").exists() and (cargo := shutil.which("cargo")):
        checks["rust-tests"] = CheckSpec(
            "rust-tests",
            (str(Path(cargo).absolute()), "test", "--locked"),
            str(root),
            {
                **path_env,
                "RUSTUP_HOME": str(Path.home() / ".rustup"),
                "CARGO_HOME": str(Path.home() / ".cargo"),
            },
            (str(root / "Cargo.toml"), str(root / "Cargo.lock")),
        )
    package = root / "package.json"
    if package.exists() and package.stat().st_size < 32768 and (npm := shutil.which("npm")):
        scripts = json.loads(package.read_text()).get("scripts", {})
        for name in ("test", "typecheck", "lint", "build"):
            script = str(scripts.get(name, ""))
            if (
                script
                and "no test specified" not in script
                and script.strip() not in {"true", "echo"}
            ):
                check_id = f"npm-{name}"
                checks[check_id] = CheckSpec(
                    check_id,
                    (str(Path(npm).absolute()), "run", name),
                    str(root),
                    dict(path_env),
                    (str(package),),
                )
    if not checks and (
        any(root.glob("*.py"))
        or any(
            (root / name).is_file() for name in ("pyproject.toml", "requirements.txt", "setup.cfg")
        )
    ):
        # Capture a fixed runner before inference when a Python project has no
        # checks. No project code is imported by discovery. An empty suite cannot
        # satisfy completion; only an approved execution provides evidence.
        checks["python-tests"] = CheckSpec(
            "python-tests",
            (executable, "-m", "unittest", "discover", "-s", ".", "-p", "test_*.py", "-q"),
            str(root),
            dict(path_env),
            source=PYTHON_BOOTSTRAP_SOURCE,
        )
    return checks


@dataclass
class TaskRecord:
    """Small-model working state independent of conversation summaries.

    A check is evidence for a criterion, never proof of arbitrary semantic correctness.
    Configuration and checks are captured before model execution. Selection cannot
    remove requirements after an effect.
    """

    workspace: str
    objective: str
    checks: dict[str, CheckSpec]
    selected: tuple[str, ...] = ()
    criteria: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] = ()
    reports: dict[str, dict[str, Any]] = field(default_factory=dict)
    source_paths: set[str] = field(default_factory=set)
    frozen: bool = False
    forbids_write: bool = False
    clarifications: dict[str, dict[str, str]] = field(default_factory=dict)

    @classmethod
    def create(cls, workspace: str, objective: str) -> TaskRecord:
        from .run_contracts import infer_run_contract

        checks = discover_checks(workspace)
        return cls(
            workspace,
            objective,
            checks,
            tuple(checks),
            (objective[:1000],),
            forbids_write=infer_run_contract(objective).forbids_write,
        )

    def plan(
        self, paths: list[str], criteria: list[str], check_ids: list[str] | None = None
    ) -> dict[str, Any]:
        if self.frozen:
            raise PlanValidationError(
                "plan_frozen",
                "The plan is frozen after the first write or final check; use the existing plan",
            )
        if (
            not criteria
            or len(criteria) > 8
            or any(not x.strip() or len(x) > 1000 for x in criteria)
        ):
            raise PlanValidationError(
                "invalid_criteria", "Provide one to eight concrete acceptance criteria"
            )
        if len(paths) > 16:
            raise PlanValidationError("invalid_paths", "A worker task may target at most 16 paths")
        if check_ids is None:
            check_ids = list(self.selected)
        if len(check_ids) > 16:
            raise PlanValidationError("invalid_check_ids", "Select at most 16 repository check IDs")
        docs_only = bool(paths) and all(p.lower().endswith((".md", ".rst", ".txt")) for p in paths)
        if any(x not in self.checks for x in check_ids):
            raise PlanValidationError(
                "unknown_check_ids",
                "Unknown check ID; choose from available_check_ids, not command text",
            )
        if not check_ids and self.checks and not docs_only:
            raise PlanValidationError(
                "check_required",
                "Select at least one available check ID, or omit check_ids to keep the default selection",
            )
        self.allowed_paths = tuple(str(safe_path(p, self.workspace)) for p in paths)
        self.criteria = tuple(criteria)
        self.selected = tuple(dict.fromkeys(check_ids))
        return self.snapshot()

    def check_for(self, arguments: dict[str, Any]) -> str | None:
        return next((key for key in self.selected if self.checks[key].matches(arguments)), None)

    def dependencies(self) -> set[str]:
        paths = set(self.source_paths)
        for key in self.selected:
            for raw in self.checks[key].paths:
                path = Path(raw)
                if path.is_dir():
                    for child in path.rglob("*"):
                        if (
                            child.is_file()
                            and not child.is_symlink()
                            and "__pycache__" not in child.parts
                        ):
                            paths.add(str(child.resolve()))
                            if len(paths) > 512:
                                raise ValueError(
                                    "Check dependencies exceed 512 files; narrow project check paths"
                                )
                elif path.exists():
                    paths.add(str(path))
        manifest = Path(self.workspace) / ".protoagent" / "project.json"
        if manifest.exists():
            paths.add(str(manifest))
        if len(paths) > 512:
            raise ValueError("Check dependencies exceed 512 files; narrow project check paths")
        return paths

    def packet(self, role: str, objective: str, paths: list[str]) -> dict[str, Any]:
        if role not in {"explorer", "coder", "tester"} or len(paths) > 8:
            raise ValueError("Use a known worker and at most eight source paths")
        sources = []
        for path in paths:
            absolute = str(safe_path(path, self.workspace))
            source = read_file(
                absolute,
                self.workspace,
                with_line_numbers=role != "coder",
                max_chars=min(2000, 6000 // max(1, len(paths))),
            )
            if source.get("success"):
                self.source_paths.add(absolute)
            sources.append(source)
        return {
            "role": role,
            "objective": objective[:1000],
            "sources": sources,
            "acceptance_criteria": list(self.criteria),
            "check_ids": list(self.selected),
            "user_clarifications": {key: dict(value) for key, value in self.clarifications.items()},
            "return_status": ["done", "needs_context", "blocked"],
        }

    def report(
        self, role: str, status: str, summary: str, missing_paths: list[str]
    ) -> dict[str, Any]:
        if role not in {"explorer", "coder", "tester"} or status not in {
            "done",
            "needs_context",
            "blocked",
        }:
            raise ValueError("Unknown worker or outcome")
        value = {
            "status": status,
            "summary": summary[:1500],
            "missing_paths": [str(safe_path(p, self.workspace)) for p in missing_paths[:8]],
        }
        self.reports[role] = value
        return value

    def snapshot(self) -> dict[str, Any]:
        return {
            "objective": self.objective[:1000],
            "acceptance_criteria": list(self.criteria),
            "allowed_paths": list(self.allowed_paths),
            "required_checks": list(self.selected),
            "available_check_ids": list(self.checks),
            "bootstrap_checks": [
                key for key, check in self.checks.items() if check.source == PYTHON_BOOTSTRAP_SOURCE
            ],
            "available_checks": [asdict(check) for check in self.checks.values()],
            "worker_reports": dict(self.reports),
            "frozen": self.frozen,
            "forbids_write": self.forbids_write,
            "user_clarifications": {key: dict(value) for key, value in self.clarifications.items()},
        }


def add_task_tools(agent, record: TaskRecord | None, role: str) -> None:
    if record is None:
        return
    suffix = ":protoagent-task-plan-2"
    if not agent.execution_version.endswith(suffix):
        agent.execution_version += suffix

    @agent.tool(capabilities=["task.manage"])
    def task_status() -> dict[str, Any]:
        """Read the current objective, criteria, available check IDs and worker outcomes."""
        return record.snapshot()

    if role == "architect":

        @agent.tool(capabilities=["task.manage"])
        def plan_task(
            paths: list[str], criteria: list[str], check_ids: list[str] | None = None
        ) -> dict[str, Any]:
            """Set criteria and write paths before editing. Omit check_ids to keep defaults; use only available_check_ids. Rejected plans return feedback without changing the plan."""
            try:
                return {"success": True, **record.plan(paths, criteria, check_ids)}
            except PlanValidationError as exc:
                return {
                    "success": False,
                    "code": exc.code,
                    "message": str(exc),
                    "available_check_ids": list(record.checks),
                    "required_checks": list(record.selected),
                    "frozen": record.frozen,
                }

        @agent.tool(capabilities=["task.manage"])
        def worker_packet(role: str, objective: str, paths: list[str]) -> dict[str, Any]:
            """Resolve bounded source references into a worker task packet without inventing source."""
            return record.packet(role, objective, paths)
    else:

        @agent.tool(capabilities=["task.manage"])
        def report_task(status: str, summary: str, missing_paths: list[str] = []) -> dict[str, Any]:
            """Return done, needs_context or blocked with bounded evidence; this never proves completion."""
            return record.report(role, status, summary, missing_paths)
