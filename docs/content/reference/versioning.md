---
title: Versioning
description: Component version sources and bump policy for the ProtoAgent monorepo.
---

ProtoAgent versions independently consumed product surfaces separately. There
is no installable root package version.

## Current Component Versions

| Component | Package name | Version | Status | Source of truth |
| --- | --- | --- | --- | --- |
| Rust CLI / TUI | `proto-cli` | `0.2.0` | Active | `cli/Cargo.toml` |
| Python core | `protoagent-core` | `0.2.0` | Active | `core/pyproject.toml` and `protoagent_core.__version__` |
| ACP server | `proto-acp` | `0.0.0-dev.0` | Planned | `acp/VERSION` |
| Documentation | `protoagent-docs` | `0.2.0` | Active release metadata | `docs/package.json` |

The ACP version is intentionally a development marker. It should not be treated
as a shipped protocol surface until `acp/` contains server code and tests.

“ProtoAgent 0.2.0” is the coordinated release train for the active CLI and
Python core. It does not imply that ACP has shipped. Dated user-visible release
notes live in the root
[CHANGELOG.md](https://github.com/nMaroulis/protoagent/blob/main/CHANGELOG.md).

## Runtime Inspection

Shell:

```bash
cargo run --locked --manifest-path cli/Cargo.toml -- version
```

Fullscreen TUI:

```text
/version
```

The shell and TUI both read component inventory through
`protoagent_core.agent_engine.component_versions()`. The Rust CLI passes its
Cargo package version into that call so the displayed CLI version follows
`cli/Cargo.toml`.

`cli/Cargo.lock` is committed for reproducible application builds. Release and
CI commands use `--locked`; dependency changes should update the lockfile in the
same change rather than allowing implicit resolution during publication.

## Bump Rules

| Change | Version action |
| --- | --- |
| CLI command, TUI, or Rust packaging behavior changes | Bump `cli/Cargo.toml` when preparing a user-facing release. |
| Core API, runtime behavior, provider config, Context Loom, or package metadata changes | Bump `core/pyproject.toml` and `core/protoagent_core/_version.py` together. |
| ACP server code or protocol compatibility changes | Replace the `0.0.0-dev.0` marker with the first real ACP prerelease or release. |
| Coordinated CLI/core release | Keep active component versions aligned and update docs metadata plus the changelog. |
| Docs-only changes | No component version bump unless the docs describe an already prepared release. |

Core version drift is checked by:

```bash
PYTHONPATH=core .venv/bin/python -m unittest core.tests.test_versioning
```

## 0.2.0 Release Order

ProtoAgent 0.2.0 requires `protolink>=0.6.6` for Scout and web-tool readiness.
Publish ProtoLink 0.6.6 to the target package index first, verify that a clean
environment can resolve it, and only then publish ProtoAgent core/CLI 0.2.0
artifacts. Do not lower the dependency floor to work around release ordering.
