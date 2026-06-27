---
title: Context Loom Internals
description: Deterministic workspace indexing, scoring, evidence packs, and prompt rendering.
---

Context Loom is ProtoAgent's deterministic, local workspace context layer. It is
not embeddings-first retrieval. It indexes text files into SQLite, scores them
against a task, and builds source-cited Context Packs.

## Files

| File | Responsibility |
| --- | --- |
| `context/schema.py` | `IndexedFile` and `ContextItem` dataclasses. |
| `context/store.py` | SQLite index path, schema, read/write/status helpers. |
| `context/indexer.py` | Workspace walk, metadata extraction, content compaction. |
| `context/packer.py` | Query terms, file scoring, snippets, pack rendering. |
| `context/__init__.py` | Public exports used by `agent_engine.py` and Explorer. |

## Storage

Indexes live under:

```text
~/.protoagent/indexes/<workspace-hash>.sqlite
```

or:

```text
${PROTOAGENT_CONFIG_DIR}/indexes/<workspace-hash>.sqlite
```

`workspace_key()` hashes the resolved workspace path to a stable 16-character
key. Each workspace gets its own SQLite file.

## Indexed Data

Each `IndexedFile` stores:

| Field | Meaning |
| --- | --- |
| `path` | Workspace-relative path. |
| `size_bytes` | File size. |
| `mtime_ns` | Modification timestamp. |
| `sha1` | Content hash. |
| `language` | Detected from extension. |
| `symbols` | Extracted definitions. |
| `imports` | Extracted import hints. |
| `headings` | Markdown headings or language-specific headings. |
| `content` | Compact non-empty content sample. |

## Index Limits

| Limit | Value |
| --- | --- |
| Max indexed files | 1200 |
| Max file bytes | 240000 |
| Max compact content chars | 80000 |

Ignored directories come from `tools.DEFAULT_IGNORES`, including `.git`,
`.venv`, `node_modules`, `target`, `dist`, and `build`.

## Metadata Extraction

| Language | Extraction path |
| --- | --- |
| Python | `ast.parse()` for classes/functions/imports, regex fallback on syntax errors. |
| Rust | Regex for functions, structs, enums, traits, impl blocks, and `use` imports. |
| JavaScript/TypeScript | Regex for functions, classes, variables, exports, imports. |
| Markdown | Heading extraction. |
| Other text | Language label only. |

## Pack Building

`build_context_pack(query, workspace, tagged_paths=None, max_items=8, refresh=True)`:

1. Refreshes the index by default.
2. Reads all indexed entries.
3. Reads `git status --short`.
4. Extracts normalized query terms.
5. Scores entries by tags, path mentions, dirty files, symbols, headings,
   imports, and content matches.
6. Selects up to eight items.
7. Builds bounded snippets around matching lines.
8. Returns a structured pack with budget and open questions.

## Scoring Signals

| Signal | Approximate effect |
| --- | --- |
| Explicitly tagged path | Strongest boost. |
| Path or basename mentioned in request | Strong boost. |
| File has git changes | Moderate boost. |
| Term in path | Strong term boost. |
| Term in symbol | Strong term boost. |
| Term in heading | Medium boost. |
| Term in import | Medium boost. |
| Term in compact content | Small boost. |

The scorer is intentionally inspectable and deterministic so smaller models do
not need to rediscover the repository from scratch.

## Pack Shape

```json
{
  "name": "Context Loom",
  "workspace": "/path/to/project",
  "query": "runtime streaming task handling",
  "terms": ["runtime", "streaming", "task", "handling"],
  "items": [
    {
      "path": "core/protoagent_core/runtime.py",
      "language": "python",
      "score": 42,
      "reason": "path mentioned in request",
      "evidence": ["matched request terms", "symbol match"],
      "symbols": ["run_selected_model"],
      "line_range": "100-110",
      "snippet": "..."
    }
  ],
  "git": {
    "success": true,
    "status": []
  },
  "budget": {
    "max_items": 8,
    "max_snippet_chars": 1600
  },
  "open_questions": []
}
```

## Prompt Rendering

`format_context_pack_for_prompt()` renders the pack as compact text:

1. It labels the pack as evidence, not a new instruction.
2. It includes workspace, path, reason, evidence, symbols, imports, headings,
   line range, snippet, and open questions.
3. `agent_engine._runtime_prompt()` bounds the combined tagged-file and Context
   Loom prompt context before appending the current user request.

## Explorer Tool

Explorer exposes Context Loom as a tool:

```python
build_context_pack(query: str)
```

This lets Architect ask Explorer for a fresh focused evidence pack during a run,
not only during initial prompt preparation.
