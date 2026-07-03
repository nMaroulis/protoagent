# ProtoAgent Versioning

ProtoAgent versions its product surfaces independently. The monorepo does not
currently publish a single root package version.

## Current Components

| Component | Package name | Version | Status | Source of truth |
| --- | --- | --- | --- | --- |
| Rust CLI / TUI | `proto-cli` | `0.1.0` | Active | `cli/Cargo.toml` |
| Python core | `protoagent-core` | `0.1.0` | Active | `core/pyproject.toml` and `protoagent_core.__version__` |
| ACP server | `proto-acp` | `0.0.0-dev.0` | Planned | `acp/VERSION` |

## Policy

- Active components use SemVer and start at `0.1.0` while the public surface is
  still moving quickly.
- The CLI and core may move independently once one surface changes without the
  other.
- ACP stays on `0.0.0-dev.0` until there is checked-in server code and a real
  protocol compatibility contract.
- Version bumps should update the source-of-truth file, the Docusaurus
  versioning reference, and any affected component README in the same change.
- Before bumping the Python core, run the versioning test:

```bash
PYTHONPATH=core .venv/bin/python -m unittest core.tests.test_versioning
```

## Inspecting Versions

From the shell:

```bash
cargo run --manifest-path cli/Cargo.toml -- version
```

From the fullscreen TUI:

```text
/version
```
