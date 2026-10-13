"""Runtime-owned task packets and frozen, repository-backed verification plans."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from protolink import ToolRejectedError

from .python_runtime import python_executable
from .tools import read_file, safe_path

PYTHON_BOOTSTRAP_SOURCE = "Python unittest bootstrap"


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
    python_project = (
        any((root / directory).is_dir() for directory in ("tests", "core/tests"))
        or any(root.glob("*.py"))
        or any(
            (root / name).is_file() for name in ("pyproject.toml", "requirements.txt", "setup.cfg")
        )
    )
    executable = python_executable(root) if python_project else ""
    if executable:
        path_env["PATH"] = os.pathsep.join([str(Path(executable).parent), path_env["PATH"]])
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
    if not checks and python_project:
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
    verification_handoff: dict[str, str] | None = None

    @classmethod
    def create(cls, workspace: str, objective: str, *, contract=None) -> TaskRecord:
        from .run_contracts import infer_run_contract

        workspace_task = (
            contract is None
            or contract.requires_write
            or (contract.task_kind == "workspace-verification")
        )
        checks = discover_checks(workspace) if workspace_task else {}
        return cls(
            workspace,
            objective,
            checks,
            tuple(checks),
            (objective[:1000],) if workspace_task else (),
            forbids_write=not contract.requires_write
            if contract is not None
            else infer_run_contract(objective).forbids_write,
        )

    def plan(
        self, paths: list[str], criteria: list[str], check_ids: list[str] | None = None
    ) -> dict[str, Any]:
        if self.frozen:
            raise self._plan_rejection(
                "plan_frozen",
                "The plan is frozen after the first write or final check; use the existing plan",
            )
        if (
            not criteria
            or len(criteria) > 8
            or any(not x.strip() or len(x) > 1000 for x in criteria)
        ):
            raise self._plan_rejection(
                "invalid_criteria", "Provide one to eight concrete acceptance criteria"
            )
        if len(paths) > 16:
            raise self._plan_rejection("invalid_paths", "A worker task may target at most 16 paths")
        docs_only = bool(paths) and all(p.lower().endswith((".md", ".rst", ".txt")) for p in paths)
        if check_ids is None or (not check_ids and not docs_only):
            check_ids = list(self.selected)
        if len(check_ids) > 16:
            raise self._plan_rejection(
                "invalid_check_ids", "Select at most 16 repository check IDs"
            )
        if any(x not in self.checks for x in check_ids):
            raise self._plan_rejection(
                "unknown_check_ids",
                "Unknown check ID; choose from available_check_ids, not command text",
            )
        if not check_ids and self.checks and not docs_only:
            raise self._plan_rejection(
                "check_required",
                "Select at least one available check ID, or omit check_ids to keep the default selection",
            )
        allowed_paths = tuple(str(safe_path(p, self.workspace)) for p in paths)
        from .run_contracts import infer_run_contract

        contract = infer_run_contract(self.objective)
        bootstrap_needs_tests = any(
            self.checks[key].source == PYTHON_BOOTSTRAP_SOURCE
            and key not in self.baseline_check_ids()
            for key in check_ids
        )
        if (
            contract.requires_write
            and not contract.allows_unverified_docs
            and bootstrap_needs_tests
            and allowed_paths
            and not any(
                Path(path).parent == Path(self.workspace) and Path(path).match("test_*.py")
                for path in allowed_paths
            )
        ):
            raise ToolRejectedError(
                "The bootstrap runner needs regression tests, but the proposed write scope excludes them",
                code="regression_scope_missing",
                details={"paths": paths, "required_test_pattern": "root test_*.py"},
                suggestion="Include a focused root test_*.py regression file alongside implementation paths, or keep the default unrestricted project scope by omitting plan_task. Do not drop required checks.",
            )
        self.allowed_paths = allowed_paths
        self.criteria = tuple(criteria)
        self.selected = tuple(dict.fromkeys(check_ids))
        return self.snapshot()

    def _plan_rejection(self, code: str, message: str) -> ToolRejectedError:
        """All plan validation precedes mutation; native rejection guarantees no change."""
        return ToolRejectedError(
            message,
            code=code,
            details={
                "available_check_ids": list(self.checks),
                "required_checks": list(self.selected),
                "frozen": self.frozen,
            },
            suggestion="Use task_status for current criteria, scope and check IDs; keep a frozen plan.",
        )

    def check_for(self, arguments: dict[str, Any]) -> str | None:
        return next((key for key in self.selected if self.checks[key].matches(arguments)), None)

    def dependencies(self) -> set[str]:
        paths = set(self.source_paths)
        for key in self.selected:
            check = self.checks[key]
            if check.source != ".protoagent/project.json" and check.argv[1:] == (
                "-m",
                "unittest",
                "discover",
                "-s",
                ".",
                "-p",
                "test_*.py",
                "-q",
            ):
                # A root runner remains frozen when tests move into packages.
                # Capture its current test inputs, including new package markers,
                # without changing the command or trusting exit zero alone.
                pending = [Path(self.workspace)]
                while pending:
                    directory = pending.pop()
                    for child in directory.iterdir():
                        if child.is_symlink():
                            continue
                        if child.is_file() and (
                            child.match("test_*.py") or child.name == "__init__.py"
                        ):
                            paths.add(str(child))
                            if len(paths) > 512:
                                raise ValueError(
                                    "Check dependencies exceed 512 files; narrow project check paths"
                                )
                        elif child.is_dir():
                            marker = child / "__init__.py"
                            if marker.is_file() and not marker.is_symlink():
                                pending.append(child)
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
            raise ToolRejectedError(
                "Use a known worker and at most eight source paths",
                code="invalid_worker_packet",
                details={"roles": ["explorer", "coder", "tester"], "max_paths": 8},
            )
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
            "executed": False,
            "next_step": f"Delegate inference to {role} with this packet. Preparing it did not run the worker.",
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
            raise ToolRejectedError(
                "Unknown worker or outcome",
                code="invalid_worker_report",
                details={
                    "roles": ["explorer", "coder", "tester"],
                    "statuses": ["done", "needs_context", "blocked"],
                },
            )
        value = {
            "status": status,
            "summary": summary[:1500],
            "missing_paths": [str(safe_path(p, self.workspace)) for p in missing_paths[:8]],
        }
        self.reports[role] = value
        return value

    def baseline_check_ids(self) -> list[str]:
        """A bootstrap runner has no existing baseline until test files exist."""
        bootstrap_ready = any(path.is_file() for path in Path(self.workspace).glob("test_*.py"))
        return [
            key
            for key in self.selected
            if self.checks[key].source != PYTHON_BOOTSTRAP_SOURCE or bootstrap_ready
        ]

    def snapshot(self) -> dict[str, Any]:
        return {
            "objective": self.objective[:1000],
            "acceptance_criteria": list(self.criteria),
            "allowed_paths": list(self.allowed_paths),
            "required_checks": list(self.selected),
            "baseline_check_ids": self.baseline_check_ids(),
            "available_check_ids": list(self.checks),
            "bootstrap_checks": [
                key for key, check in self.checks.items() if check.source == PYTHON_BOOTSTRAP_SOURCE
            ],
            "available_checks": [asdict(check) for check in self.checks.values()],
            "worker_reports": dict(self.reports),
            "frozen": self.frozen,
            "forbids_write": self.forbids_write,
            "user_clarifications": {key: dict(value) for key, value in self.clarifications.items()},
            "verification_handoff": dict(self.verification_handoff)
            if self.verification_handoff
            else None,
        }


def add_task_tools(agent, record: TaskRecord | None, role: str) -> None:
    if record is None:
        return
    suffix = ":protoagent-task-plan-5"
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
            """Set criteria and write paths before editing. Omitted/empty check_ids keep defaults for code; use only available_check_ids. Include root test_*.py files for a bootstrap runner without tests. Invalid plans receive native no-change rejection feedback."""
            return {"success": True, **record.plan(paths, criteria, check_ids)}

        @agent.tool(capabilities=["task.manage"])
        def worker_packet(
            role: Literal["explorer", "coder", "tester"], objective: str, paths: list[str]
        ) -> dict[str, Any]:
            """Prepare context only for explorer, coder or tester; then delegate inference to that worker with the packet. This tool does not run a worker or edit files."""
            return record.packet(role, objective, paths)
    else:

        @agent.tool(capabilities=["task.manage"])
        def report_task(status: str, summary: str, missing_paths: list[str] = []) -> dict[str, Any]:
            """Return done, needs_context or blocked with bounded evidence; this never proves completion."""
            return record.report(role, status, summary, missing_paths)
