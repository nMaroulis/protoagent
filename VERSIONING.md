# Versioning

ProtoAgent is a monorepo, not a single installable package. Each independently
consumed component owns its version and follows semantic versioning.

## Component Inventory

| Component | Package/binary | Current version | Status | Source of truth |
| --- | --- | --- | --- | --- |
| Rust CLI/TUI | `proto-cli` | `0.2.0` | Active | `cli/Cargo.toml` |
| Python core | `protoagent-core` | `0.2.0` | Active | `core/pyproject.toml` and `protoagent_core.__version__` |
| ACP adapter | `proto-acp` | `0.0.0-dev.0` | Planned | Documentation only until implementation begins |
| Documentation site | `protoagent-docs` | `0.2.0` | Active release metadata | `docs/package.json` |

“ProtoAgent 0.2.0” is the coordinated release train for the active CLI and core
components. It does not create a root package version, and it does not imply
that the planned ACP adapter is shipped.

## Policy

- Active components use SemVer while their public interfaces are still
  evolving.
- CLI-only changes bump `cli/Cargo.toml`.
- Python core API/runtime changes bump `core/pyproject.toml` and
  `core/protoagent_core/_version.py` together.
- Coordinated user-facing releases keep the CLI and core on the same version.
- The docs package version tracks the coordinated release it documents.
- The ACP version remains `0.0.0-dev.0` until an installable server and protocol
  surface exist.
- Release notes belong in the root [CHANGELOG.md](CHANGELOG.md).

## Release Checklist

1. Confirm required ProtoLink release `0.6.6` is available from the target
   package index. Publish ProtoLink first; ProtoAgent 0.2.0 cannot install from
   that index before its dependency exists.
2. Update the source-of-truth component versions and runtime version inventory.
3. Update tests that assert exact versions.
4. Align the ProtoLink dependency floor across package metadata, development
   commands, and CI.
5. Update CLI help/status text, README files, docs, and the whitepaper for
   user-visible behavior.
6. Add a dated changelog entry with migration or trust-boundary notes.
7. Run Python tests and quality checks, Rust checks with the committed
   `cli/Cargo.lock` and `--locked`, and the Docusaurus build.
8. Verify wheel/sdist and release-binary metadata before publishing artifacts.
