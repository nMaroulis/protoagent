# Contributing to ProtoAgent

Thanks for helping make ProtoAgent better. This repository is a small monorepo:
the agent runtime lives in Python under `core/`, the terminal interface lives in
Rust under `cli/`, and the docs live under `docs/`.

## Development Setup

Use Python 3.12 or newer and a current Rust toolchain.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install "protolink[http,llms]>=0.6.5" ruff ty
rustup component add rustfmt clippy
cargo build --manifest-path cli/Cargo.toml
```

## Project Boundaries

- Put Python runtime logic in `core/protoagent_core/`.
- Keep `cli/` focused on the Rust terminal experience and the PyO3 bridge.
- Update docs in the same pull request when commands, settings, runtime behavior,
  or user-visible workflows change.
- Do not commit generated folders such as `.venv/`, `docs/build/`,
  `docs/.docusaurus/`, or `docs/node_modules/`.

## Git Workflow

Create a focused branch for each change:

```bash
git checkout -b feature/short-description
git status
git add CONTRIBUTING.md pyproject.toml rustfmt.toml
git commit -m "docs: add contributor tooling guide"
```

Use clear commit messages and keep unrelated cleanup in a separate pull request.

## Quality Checks

Run the checks that match your change before opening a pull request.

```bash
# Python style, linting, and type checking
ruff format --check .
ruff check .
ty check --extra-search-path core

# Python core tests
PYTHONPATH=core .venv/bin/python -m unittest discover core/tests

# Rust formatting, linting, and tests
cargo fmt --manifest-path cli/Cargo.toml -- --check
cargo clippy --manifest-path cli/Cargo.toml --all-targets -- -D warnings
cargo test --manifest-path cli/Cargo.toml

# Documentation site
cd docs
npm run build
```

Ruff is configured for all checked-in Python source. Ty currently focuses on the
core runtime and the CLI compatibility shim so the main product surface gets a
clean type-checking gate first.

GitHub Actions runs the Python formatting, linting, and type-checking gate on
every push and pull request through `.github/workflows/python-quality.yml`.

## Pull Requests

- Keep pull requests focused and explain the user-facing impact.
- Include tests for behavior changes and regression fixes when practical.
- Prefer concrete command output in the PR description when a change touches the
  CLI, runtime, or docs build.
- Call out skipped checks and why they were skipped.
