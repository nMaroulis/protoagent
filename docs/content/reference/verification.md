---
title: Verification
description: Commands for checking the CLI, core, and docs.
---

Use this page before publishing changes.

## Python Core

```bash
PYTHONPATH=core .venv/bin/python -m unittest discover core/tests
```

Targeted tests:

```bash
PYTHONPATH=core .venv/bin/python -m unittest core.tests.test_runtime_integration
PYTHONPATH=core .venv/bin/python -m unittest core.tests.test_history_integration
PYTHONPATH=core .venv/bin/python -m unittest core.tests.test_llm_context
PYTHONPATH=core .venv/bin/python -m unittest core.tests.test_help_agent
```

## Rust CLI

```bash
cargo test --manifest-path cli/Cargo.toml
cargo check --manifest-path cli/Cargo.toml
```

Runtime diagnostics:

```bash
cargo run --manifest-path cli/Cargo.toml -- check
```

No-model smoke test:

```bash
PROTOAGENT_SCAFFOLD=1 cargo run --manifest-path cli/Cargo.toml -- run "show diagnostics"
```

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
PROTOAGENT_TRACE=1 cargo run --manifest-path cli/Cargo.toml -- run "your failing task" 2>&1 | tee /tmp/protoagent-debug.txt
tail -n 80 "${PROTOAGENT_CONFIG_DIR:-$HOME/.protoagent}/traces.jsonl"
```

Attach both `/tmp/protoagent-debug.txt` and the trace tail if available.
