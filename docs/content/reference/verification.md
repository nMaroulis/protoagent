---
title: Verification
description: Commands for checking the CLI, core, and docs.
---

Use this page before publishing changes.

## Python Core

Style, lint, and type-check the Python surface:

```bash
ruff format --check .
ruff check .
ty check --extra-search-path core
```

Ty is currently scoped to the core runtime and the CLI compatibility shim in the
repo-level `pyproject.toml`.

GitHub Actions runs these Python quality checks on every push and pull request
through `.github/workflows/python-quality.yml`.

Run the Python unit tests:

```bash
PYTHONPATH=core .venv/bin/python -m unittest discover core/tests
```

Targeted tests:

```bash
PYTHONPATH=core .venv/bin/python -m unittest core.tests.test_runtime_integration
PYTHONPATH=core .venv/bin/python -m unittest core.tests.test_history_integration
PYTHONPATH=core .venv/bin/python -m unittest core.tests.test_llm_context
PYTHONPATH=core .venv/bin/python -m unittest core.tests.test_help_agent
PYTHONPATH=core .venv/bin/python -m unittest core.tests.test_quality_eval
PYTHONPATH=core .venv/bin/python -m unittest core.tests.test_scout
PYTHONPATH=core .venv/bin/python -m unittest core.tests.test_context_indexer
PYTHONPATH=core .venv/bin/python -m unittest core.tests.test_versioning
```

## Rust CLI

```bash
cargo fmt --manifest-path cli/Cargo.toml -- --check
cargo clippy --locked --manifest-path cli/Cargo.toml --all-targets -- -D warnings
cargo test --locked --manifest-path cli/Cargo.toml
cargo check --locked --manifest-path cli/Cargo.toml
```

If formatting or Clippy are missing from a local Rust toolchain, install them
with:

```bash
rustup component add rustfmt clippy
```

Runtime diagnostics:

```bash
cargo run --locked --manifest-path cli/Cargo.toml -- check
```

Component version inventory:

```bash
cargo run --locked --manifest-path cli/Cargo.toml -- version
```

No-model smoke test:

```bash
PROTOAGENT_SCAFFOLD=1 cargo run --locked --manifest-path cli/Cargo.toml -- run "show diagnostics"
```

Prompt-profile eval smoke:

```bash
cargo run --locked --manifest-path cli/Cargo.toml -- eval profiles --limit 3
```

Use `--live` only when a model is configured. Live evals auto-deny workspace
write approvals so Coder behavior can be measured without applying changes.

## Docusaurus Docs

```bash
cd docs
npm install
npm run build
```

Local preview:

```bash
npm run start
```

The generated build output is ignored at `docs/build/`.

## End-To-End Debug Artifact

For shareable failing-run evidence:

```bash
PROTOAGENT_TRACE=1 cargo run --locked --manifest-path cli/Cargo.toml -- run "your failing task" 2>&1 | tee /tmp/protoagent-debug.txt
tail -n 80 "${PROTOAGENT_CONFIG_DIR:-$HOME/.protoagent}/traces.jsonl"
```

Attach both `/tmp/protoagent-debug.txt` and the trace tail if available.
