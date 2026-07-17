---
title: Installation
description: Set up Python, ProtoLink, Rust, and the docs site.
---

ProtoAgent currently runs from source. The Rust CLI embeds the Python core with
PyO3, so both the Rust toolchain and the Python environment must be available.

## Prerequisites

| Tool | Why it is needed |
| --- | --- |
| Python 3.12 or newer | Runs `core/protoagent_core` and ProtoLink. |
| Rust and Cargo 1.83 or newer | Builds `cli/`, including PyO3 0.28. |
| A model provider | Ollama, LM Studio, llama.cpp, OpenAI-compatible local server, or a cloud API provider. |
| Node.js 18 or newer | Runs the Docusaurus documentation site. |

## Clone And Create Python Environment

```bash
git clone https://github.com/nMaroulis/protoagent.git
cd protoagent

python3 -m venv .venv
source .venv/bin/activate
pip install "protolink[http,llms]>=0.6.6"
pip install -e core
```

The core imports are resolved from `core/` by the Rust CLI at runtime. The CLI
also checks common `.venv/lib/python3.x/site-packages` paths so ProtoLink and
provider extras can be found when the binary starts from either the repo root or
the `cli/` folder. The editable core install exposes the `protoagent-core`
package metadata and keeps `protoagent_core.__version__` available to tools.

ProtoLink 0.6.6 is the minimum for optional Scout's first-party
`web_search`/`fetch_url` tools and the `web_tools_ready` diagnostic.
If a package index does not yet contain 0.6.6, ProtoLink must be published there
before ProtoAgent 0.2.0 can be installed from that index.

## Build The CLI

```bash
cargo build --locked --manifest-path cli/Cargo.toml
```

For release builds:

```bash
cargo build --release --locked --manifest-path cli/Cargo.toml
```

The crate name is `proto-cli`. You can run it through Cargo while developing:

```bash
cargo run --locked --manifest-path cli/Cargo.toml -- start
```

Many user-facing examples use `proto-cli` or `protoagent` as the installed
binary name. During development, replace that with the `cargo run` form above.

Check component versions with:

```bash
cargo run --locked --manifest-path cli/Cargo.toml -- version
```

## Install The Docs Site

The Docusaurus site lives in `docs/`.

```bash
cd docs
npm install
npm run start
```

The site is configured to serve the docs from `docs/content/`, not from the
default nested `docs/docs/` path. That keeps documentation pages separate from
site configuration and styling.

## Optional Local Model Setup

Ollama is the default local provider. If you use Ollama, install and pull a code
model first:

```bash
ollama pull qwen2.5-coder:7b
```

Then select it in ProtoAgent:

```bash
cargo run --locked --manifest-path cli/Cargo.toml -- model
```

You can also use the TUI `/model` command after starting the fullscreen UI.

## Environment Isolation

By default, local config and state live under `~/.protoagent`. For repeatable
tests or demos, point ProtoAgent at a disposable config directory:

```bash
export PROTOAGENT_CONFIG_DIR=/tmp/protoagent-demo
```

This redirects provider config, project config, session summaries, Context Loom
indexes, ProtoLink conversation storage, and trace output.
