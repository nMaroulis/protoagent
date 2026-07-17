---
title: Documentation Maintenance
description: How to keep the modular docs aligned with the codebase.
---

ProtoAgent changes quickly, so the docs are structured as a maintenance map.
Update docs in the same pull request as code changes when user-visible behavior,
runtime contracts, or architecture boundaries change.

## Update Rules

| Change | Required docs update |
| --- | --- |
| New shell command | `CLI / Command Reference` |
| New slash command | `CLI / Command Reference` and possibly a focused CLI page |
| New TUI panel or modal | `CLI / Fullscreen TUI` |
| New provider or config key | `CLI / Models And Config`, `Core / Config And Models`, `Reference / Environment` |
| New runtime env var | `Reference / Environment` |
| New component version or bump policy | `Reference / Versioning`, affected component README, `VERSIONING.md` |
| New agent, tool, or capability | `Core / Agent Deck`, `Core / Safety And Tools` |
| New optional/network capability | Also update `CLI / Commands`, `Reference / Environment`, trust-boundary copy, and readiness output |
| New Context Loom signal or schema field | `Core / Context Loom Internals`, `CLI / Context Loom In The CLI` |
| New memory behavior | `Core / State And Memory`, `CLI / Projects And Sessions` |
| New lint, type-check, formatter, or contributor workflow | `Contributing / Development Workflow`, `Reference / Verification` |
| ACP implementation | `ACP / Overview`, `ACP / Plan`, `Reference / File Map` |
| User-visible release | Root/component READMEs, `VERSIONING.md`, root `CHANGELOG.md`, and docs package metadata |

## Writing Style

Prefer:

1. Concrete commands.
2. Tables for command/reference material.
3. Short source-file maps.
4. Diagrams for data flow.
5. Clear "current status" labels when behavior is planned but not implemented.

Avoid:

1. Promising ACP behavior before server code exists.
2. Describing `sessions.json` as model memory.
3. Duplicating long source comments.
4. Hiding safety boundaries behind marketing language.

## Page Ownership

Each doc page should have a narrow reason to change:

| Page group | Owns |
| --- | --- |
| Getting Started | User setup and first successful run. |
| CLI | Terminal behavior and commands. |
| Core | Python and ProtoLink runtime contracts. |
| ACP | Planned editor integration until implementation lands. |
| Playground | Sample target apps only. |
| Reference | File map, environment, troubleshooting, verification. |

## Docusaurus Structure

| Path | Purpose |
| --- | --- |
| `docs/content/` | Markdown docs. |
| `docs/sidebars.js` | Sidebar ordering and categories. |
| `docs/docusaurus.config.js` | Site configuration. |
| `docs/src/pages/index.jsx` | Homepage. |
| `docs/src/css/custom.css` | Global styling. |
| `docs/static/img/banner.jpeg` | Local copy of the repo banner. |

## Before Merging Docs Changes

Run:

```bash
cd docs
npm run build
```

If you changed commands or runtime behavior, also run the relevant code checks:

```bash
cargo test --locked --manifest-path cli/Cargo.toml
PYTHONPATH=core .venv/bin/python -m unittest discover core/tests
```
