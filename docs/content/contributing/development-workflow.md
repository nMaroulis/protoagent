---
title: Development Workflow
description: Contributor setup, quality gates, and pull request expectations.
---

ProtoAgent has three active development surfaces: the Rust CLI in `cli/`, the
Python runtime in `core/protoagent_core/`, and the Docusaurus site in `docs/`.
Use this page as the contributor checklist before opening a pull request.

## Setup

Install the Python and Rust tooling from the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install "protolink[http,llms]>=0.6.5" ruff ty
python -m pip install -e core
rustup component add rustfmt clippy
cargo build --manifest-path cli/Cargo.toml
```

The root `pyproject.toml` configures Ruff and ty. The root `rustfmt.toml`
configures Rust formatting for the CLI crate.

## Boundaries

| Surface | Path | Keep here |
| --- | --- | --- |
| Python core | `core/protoagent_core/` | ProtoLink runtime wiring, agents, Context Loom, model config, state, and tools. |
| Rust CLI | `cli/` | Terminal UX, PyO3 calls, project/session state, approvals, traces, and cancellation controls. |
| Versioning | `cli/Cargo.toml`, `core/pyproject.toml`, `acp/VERSION` | Component release markers and version inventory. |
| Docs | `docs/content/` | User-facing guides, reference pages, and contributor process. |
| Playground | `playground/` | Sample apps used as agent targets and integration sandboxes. |

Do not commit generated folders such as `.venv/`, `docs/build/`,
`docs/.docusaurus/`, or `docs/node_modules/`.

## Quality Gates

Run the checks that match the surface you touched.

### Python

```bash
ruff format --check .
ruff check .
ty check --extra-search-path core
PYTHONPATH=core .venv/bin/python -m unittest discover core/tests
PYTHONPATH=core .venv/bin/python -m unittest core.tests.test_versioning
```

Ruff covers checked-in Python source under `core/`, `cli/python/`, and
`playground/`. Ty currently focuses on `core/protoagent_core/` plus the
`cli/python/` compatibility shim so the product runtime has the first stable
type-checking gate.

### Rust

```bash
cargo fmt --manifest-path cli/Cargo.toml -- --check
cargo clippy --manifest-path cli/Cargo.toml --all-targets -- -D warnings
cargo test --manifest-path cli/Cargo.toml
cargo check --manifest-path cli/Cargo.toml
```

If `cargo fmt` or `cargo clippy` is unavailable, install the components with:

```bash
rustup component add rustfmt clippy
```

### Docs

```bash
cd docs
npm run build
```

Update docs in the same pull request when commands, configuration, runtime
behavior, or user-facing workflows change.

## Continuous Integration

GitHub Actions runs the Python formatting, linting, and type-checking gate on
every push and pull request through `.github/workflows/python-quality.yml`.

That workflow installs `ruff`, `ty`, and the ProtoLink runtime extras, then runs:

```bash
ruff format --check .
ruff check .
ty check --extra-search-path core
```

## Pull Requests

Keep pull requests focused. Include tests for behavior changes and regression
fixes when practical, paste the relevant command output into the PR description,
and call out any checks that were skipped with the reason.
