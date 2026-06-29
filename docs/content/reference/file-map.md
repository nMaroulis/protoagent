---
title: File Map
description: Source files and the documentation pages that should be updated with them.
---

Use this map when changing code. It names the primary source files and the docs
pages that should usually move with them.

## Rust CLI

| Source | Update docs |
| --- | --- |
| `cli/src/main.rs` | `CLI / Commands`, `CLI / Models And Config`, `CLI / Context Loom In The CLI`, `Reference / Environment` |
| `cli/src/terminal_ui.rs` | `CLI / Commands`, `CLI / Fullscreen TUI`, `CLI / Safety, Tracing, And Cancellation` |
| `cli/src/terminal_ui/render.rs` | `CLI / Fullscreen TUI` |
| `cli/src/terminal_ui/project.rs` | `CLI / Projects And Sessions`, `CLI / Fullscreen TUI` |
| `cli/src/terminal_ui/model_picker.rs` | `CLI / Models And Config`, `Getting Started / Provider Setup` |
| `cli/src/terminal_ui/approval.rs` | `CLI / Safety, Tracing, And Cancellation` |
| `cli/src/terminal_ui/diff_view.rs` | `CLI / Safety, Tracing, And Cancellation` |
| `cli/src/progress.rs` | `CLI / Safety, Tracing, And Cancellation`, `Core / Runtime` |
| `cli/src/timeline.rs` | `CLI / Safety, Tracing, And Cancellation` |
| `cli/src/sessions.rs` | `CLI / Projects And Sessions`, `Core / State And Memory` |

## Python Core

| Source | Update docs |
| --- | --- |
| `core/protoagent_core/agent_engine.py` | `Core / Core Architecture`, `CLI / Commands`, `Reference / Environment` |
| `core/protoagent_core/runtime.py` | `Core / Runtime`, `CLI / Safety, Tracing, And Cancellation` |
| `core/protoagent_core/runtime_bridge.py` | `Core / Runtime`, `CLI / Safety, Tracing, And Cancellation` |
| `core/protoagent_core/history.py` | `Core / State And Memory`, `CLI / Projects And Sessions` |
| `core/protoagent_core/llm.py` | `Core / Config And Models`, `Reference / Environment` |
| `core/protoagent_core/models.py` | `Core / Config And Models`, `CLI / Models And Config` |
| `core/protoagent_core/config.py` | `Core / Config And Models`, `Getting Started / Provider Setup` |
| `core/protoagent_core/prompt_profiles.py` | `Core / Agent Deck`, `CLI / Commands`, `CLI / Models And Config` |
| `core/protoagent_core/quality_eval.py` | `Core / Quality Evals`, `CLI / Commands`, `Reference / Verification` |
| `core/protoagent_core/tools.py` | `Core / Safety And Tools` |
| `core/protoagent_core/help_agent.py` | `Core / Agent Deck`, `CLI / Commands` |
| `core/protoagent_core/agents/*.py` | `Core / Agent Deck`, `Core / Safety And Tools` |
| `core/protoagent_core/context/*.py` | `Core / Context Loom Internals`, `CLI / Context Loom In The CLI` |

## Tooling And Contribution Workflow

| Source | Update docs |
| --- | --- |
| `pyproject.toml` | `Contributing / Development Workflow`, `Reference / Verification` |
| `rustfmt.toml` | `Contributing / Development Workflow`, `Reference / Verification` |
| `CONTRIBUTING.md` | `Contributing / Development Workflow` |
| `.github/workflows/python-quality.yml` | `Contributing / Development Workflow`, `Reference / Verification` |
| `.gitignore` | `Contributing / Development Workflow` when generated or local-only paths change |

## Planning And Samples

| Source | Update docs |
| --- | --- |
| `acp/README.md` or new ACP files | `ACP / Overview`, `ACP / Plan` |
| `playground/recipe-reco/*` | `Playground / Overview` |
| `playground/taskflow/*` | `Playground / Overview` |
| `whitepaper.md` | `Intro`, `Core / Agent Deck`, `Core / Context Loom Internals` |

## Docs Site

| Source | Purpose |
| --- | --- |
| `docs/docusaurus.config.js` | Docusaurus site config, nav, footer, Mermaid, code highlighting. |
| `docs/sidebars.js` | Documentation taxonomy. |
| `docs/src/pages/index.jsx` | Landing page. |
| `docs/src/pages/index.module.css` | Landing page styling. |
| `docs/src/css/custom.css` | Global Docusaurus theme overrides. |
| `docs/content/` | Documentation pages. |
