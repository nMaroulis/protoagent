---
title: Versioning
description: Component version sources and bump policy for the ProtoAgent monorepo.
---

ProtoAgent versions its product surfaces independently. There is no root
monorepo package version yet.

## Current Component Versions

| Component | Package name | Version | Status | Source of truth |
| --- | --- | --- | --- | --- |
| Rust CLI / TUI | `proto-cli` | `0.1.0` | Active | `cli/Cargo.toml` |
| Python core | `protoagent-core` | `0.1.0` | Active | `core/pyproject.toml` and `protoagent_core.__version__` |
| ACP server | `proto-acp` | `0.0.0-dev.0` | Planned | `acp/VERSION` |

The ACP version is intentionally a development marker. It should not be treated
as a shipped protocol surface until `acp/` contains server code and tests.

## Runtime Inspection

Shell:

```bash
cargo run --manifest-path cli/Cargo.toml -- version
```

Fullscreen TUI:

```text
/version
```

The shell and TUI both read component inventory through
`protoagent_core.agent_engine.component_versions()`. The Rust CLI passes its
Cargo package version into that call so the displayed CLI version follows
`cli/Cargo.toml`.

## Bump Rules

| Change | Version action |
| --- | --- |
| CLI command, TUI, or Rust packaging behavior changes | Bump `cli/Cargo.toml` when preparing a user-facing release. |
| Core API, runtime behavior, provider config, Context Loom, or package metadata changes | Bump `core/pyproject.toml` and `core/protoagent_core/_version.py` together. |
| ACP server code or protocol compatibility changes | Replace the `0.0.0-dev.0` marker with the first real ACP prerelease or release. |
| Docs-only changes | No component version bump unless the docs describe an already prepared release. |

Core version drift is checked by:

```bash
PYTHONPATH=core .venv/bin/python -m unittest core.tests.test_versioning
```
