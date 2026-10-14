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
python -m pip install -e core -r requirements-dev.txt
rustup component add rustfmt clippy
cargo build --locked --manifest-path cli/Cargo.toml
```

The root `pyproject.toml` configures Ruff and ty. `requirements-dev.txt` pins
their tested versions and the Python build frontend so contributor and CI
checks agree. The root `rustfmt.toml` configures Rust formatting for the CLI crate.
The CLI uses PyO3 0.29.3 and the committed Cargo lockfile; Rust 1.85 remains the
declared minimum, with stable 1.99 used for release validation.

Update through `pip install -U -e core` so the core's compatibility constraints
apply. In particular, ProtoLink 0.8.1's Anthropic adapter requires the pre-1.0
SDK's generation parameters; upgrading Anthropic independently to 1.x breaks
that adapter. ELK stays on 0.1.9 to satisfy Docusaurus's Mermaid theme.

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
python -m pip check
python -m build core
```

Ruff covers checked-in Python source under `core/`, `cli/python/`, and
`playground/`. Ty currently focuses on `core/protoagent_core/` plus the
`cli/python/` compatibility shim so the product runtime has the first stable
type-checking gate.

### Rust

```bash
cargo fmt --manifest-path cli/Cargo.toml -- --check
cargo clippy --locked --manifest-path cli/Cargo.toml --all-targets -- -D warnings
cargo test --locked --manifest-path cli/Cargo.toml
cargo check --locked --manifest-path cli/Cargo.toml
cargo build --release --locked --manifest-path cli/Cargo.toml
cargo build --locked --manifest-path cli/Cargo.toml
PROTOAGENT_TEST_CLI="$PWD/cli/target/debug/proto-cli" .venv/bin/python cli/tests/tui_smoke.py
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

GitHub Actions runs Python and Rust quality gates on pushes and pull requests
through `.github/workflows/python-quality.yml` and
`.github/workflows/cli-quality.yml`.

The Python workflow installs the core's declared ProtoLink runtime extras and
the tools pinned in `requirements-dev.txt`, then runs:

```bash
ruff format --check .
ruff check .
ty check --extra-search-path core
PYTHONPATH=core python -m unittest discover -s core/tests -q
```

The CLI workflow installs the embedded core, checks Rust formatting, runs
Clippy and the CLI test suite with the committed `cli/Cargo.lock` and `--locked`.
Release builds and terminal smoke tests are additional local release gates.
Documentation CI uses Node 24 and `npm ci` with the committed docs lockfile.

## Pull Requests

Keep pull requests focused. Include tests for behavior changes and regression
fixes when practical, paste the relevant command output into the PR description,
and call out any checks that were skipped with the reason.
