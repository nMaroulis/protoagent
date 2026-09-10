# Changelog

This file records user-visible changes to the active ProtoAgent components.

## [0.2.1] - 2026-09-10

### Added

- Multiline TUI composer with Ctrl-J newlines, literal bracketed paste, grapheme
  editing, terminal-cell wrapping, fuzzy slash-command completion on Tab, and
  searchable prompt history on Ctrl-R.
- Tool-only **Verifier** for approved test/build/lint commands. Architect
  delegates through ProtoLink; `shell.execute` approval shows exact argv,
  working directory, timeout, and host access. Output retention is 32 KiB and
  command timeout is bounded to 1–600 seconds. POSIX cancellation/timeout kills
  the process group; other platforms kill the direct child.
- Measured verification evidence in final responses, ProtoLink events, and run
  report metadata. Later Coder mutations invalidate earlier checks; command
  retries retain their full result history.
- Per-file checkpoints for Coder writes, plus `checkpoints` / `/checkpoints`
  and `undo [id]` / `/undo [id]`. Recovery needs no model, preserves previous
  bytes and modes, and requires a fresh ProtoLink write approval.

### Changed

- Target ProtoLink **0.6.9** across core dependencies, CI, installation docs,
  and runtime guidance. Keep delegation, actions, approvals, task cancellation,
  budget checks, events, and reports in ProtoLink.
- Teach Architect to identify real project checks and attempt at most two
  focused repairs after failures, stopping on denial or a blocker.
- Align active CLI/core/docs versions to 0.2.1; ACP remains planned.
- Require Rust/Cargo 1.85 or newer for the Unicode grapheme editor dependency.
- Refresh the operator manual with terminal styling, local monospace fonts,
  neon accents, updated docstrings and help, and verification/recovery guides.

### Fixed

- Reject stale Coder write previews and refuse undo when subsequent edits would
  be overwritten. Checkpoints cover Coder changes, not command side effects.
- Keep command approvals from satisfying workspace-write completion contracts;
  requests to run existing tests no longer imply a source modification.
- Skip symlink entries during search, Context Loom indexing, and file picking,
  preventing outside-file reads and directory cycles during enumeration.
- Bound modal sizes on narrow terminals instead of panicking on inverted limits.
- Correct obsolete direct-tool budget guidance for ProtoLink 0.6.9.

### Execution Boundary

Approved commands inherit host permissions and environment and may modify files
or use the network, including with Scout disabled. Their project working
directory is not a sandbox. Checkpoints restore individual Coder writes only;
verification freshness tracks agent mutations within the current run.

## [0.2.0] - 2026-07-17

Release prerequisite: publish ProtoLink 0.6.6 to the target package index
before publishing ProtoAgent 0.2.0; this release intentionally requires
`protolink>=0.6.6`.

### Added

- Added optional **Scout**, a stateless external-research agent backed by
  ProtoLink 0.6.6 `web_search` and `fetch_url` tools. Scout is disabled by
  default and can be toggled through `proto-cli agents scout` or
  `/agents scout`.
- Added Scout and first-party web-tool readiness to the agent and doctor
  surfaces, including the explicit `network.read` trust boundary.
- Added coordinated CLI/core agent settings so prompt profiles and optional
  agents can be inspected from the terminal.

### Changed

- Raised the runtime integration target to ProtoLink 0.6.6 and delegated web
  search, URL fetching, agent discovery, tools, state, events, policy,
  cancellation, transports, and reports to first-party ProtoLink surfaces.
- Made Context Loom refresh incremental: unchanged files are skipped using
  stored size and modification-time metadata, while changed/new files are
  reparsed and stale entries are removed.
- Tightened small-model operation with narrower role/tool boundaries,
  deterministic Context Packs, prompt profiles, and runtime completion checks.
- Expanded CLI agent controls and status text to make Scout, readiness, memory,
  prompt profile, and next-run behavior visible.
- Added release-oriented package metadata, a root MIT license, and a committed
  CLI lockfile for reproducible `--locked` builds.
- Reworked README, whitepaper, and maintainer docs around the shipped
  CLI/core boundary; the ACP adapter is now consistently marked as planned.

### Fixed

- Corrected stale install requirements, broken repository links, obsolete
  runtime claims, and documentation that confused ProtoAgent application
  contracts with ProtoLink-owned runtime contracts.
- Corrected GitHub Pages documentation links and base-path asset handling.
