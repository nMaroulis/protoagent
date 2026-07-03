---
title: ACP Plan
description: Planned implementation phases for the ProtoAgent ACP server.
---

This is the current plan for the ACP surface. It is intentionally written as a
roadmap because `acp/` does not yet contain server code.

ACP is currently versioned as `0.0.0-dev.0` in `acp/VERSION`. Keep that marker
until the first server skeleton creates a real compatibility surface.

## Phase 1: Minimal Server Skeleton

Goal: create a stdio ACP server that can start, report capabilities, and accept
a simple user request.

Planned work:

1. Add `acp/server.py`.
2. Decide the first ACP prerelease version and replace `0.0.0-dev.0` when the
   skeleton is actually usable.
3. Define protocol message parsing and response serialization.
4. Add a small adapter around `protoagent_core.agent_engine`.
5. Support basic request/response tasks without editor-side file writes.
6. Add tests for protocol frames and error handling.

Docs to update:

1. `ACP / Overview`
2. `ACP / Plan`
3. `Reference / File Map`
4. Root `README.md` editor setup section

## Phase 2: Core Runtime Integration

Goal: route editor tasks through the same ProtoLink mesh as the CLI.

Planned work:

1. Resolve workspace from ACP session metadata.
2. Reuse Context Loom pack building.
3. Pass stable session ids into ProtoLink state.
4. Stream progress as editor-compatible updates.
5. Return structured answer, trace, timeline, and diff metadata.

Important constraint: do not fork the runtime path. ACP should adapt the same
core response shape instead of introducing a second agent engine.

## Phase 3: Approval And Diff Flow

Goal: map ProtoLink approval requests into editor-native review interactions.

Planned work:

1. Create an ACP approval bridge equivalent to `RuntimeBridge`.
2. Preserve `RunAction` and `text/x-diff` preview artifacts.
3. Let the editor approve or reject before `write_file()` executes.
4. Return clear denied/canceled statuses.

Do not bypass Coder policy by applying diffs directly from the server.

## Phase 4: MCP Tool Mapping

Goal: let editor-provided MCP tools become safe ProtoAgent capabilities.

Planned work:

1. Inspect incoming tool schemas from the editor.
2. Classify tools as read-only, write-capable, network, or external side effect.
3. Route read-only tools to Explorer when safe.
4. Route write-capable tools through the same approval boundary as workspace writes.
5. Add visible docs for tool classification and limitations.

## Phase 5: Editor Guides

Goal: document setup for specific editors after the server exists.

Candidate pages:

1. Zed setup.
2. JetBrains setup.
3. Troubleshooting stdio startup.
4. Workspace trust and approval model.

## Open Questions

| Question | Current answer |
| --- | --- |
| Should ACP call `process_prompt()` directly? | Likely yes for Phase 1 and 2. |
| Should ACP share `RuntimeBridge`? | Probably not directly; it needs editor-native IO, but it should preserve the same approval semantics. |
| Should ACP own provider config? | No. Reuse `core/protoagent_core/config.py`. |
| Should ACP support model picking? | Later. Initial editor integration can rely on CLI/config setup. |
