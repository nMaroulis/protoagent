# Changelog

This file records user-visible changes to the active ProtoAgent components.

## [0.2.3] - Unreleased

### Added

- Live model generation and delegated command stdout/stderr in shell and TUI,
  using native `RunHandle.events()` and explicit streaming Agent capabilities.
  TUI previews retain up to four streams with the last 4096 characters each;
  `llm_final` replaces its generation preview. The terminal task establishes
  overall completion, including failure, cancellation and uncertainty.
- Shell Ctrl-C requests native cancellation and waits for runtime cleanup.
- Provider-free gated streaming tests for early HTTP model delivery, JSON-action
  and native-tool modes, cancellation, cleanup, live delegated process output,
  receipt deduplication, redacted persistence and checkpoint pagination.

### Changed

- Require ProtoLink 0.7.1; coordinate CLI/core/docs versions at 0.2.3.
- Consume propagated worker receipts directly from native parent/Graph tasks.
  Remove `ApplicationRunStore.trace_report()` and stored-worker evidence scans.
- Configure `SQLiteRunStore(..., redaction_policy=...)` for automatic task,
  report and caller metadata writes. Remove save overrides and custom credential
  replacement in favor of native `RedactionPolicy.sensitive_values`.
- Use native checkpoint `list_changes()` with state/run filters and pagination.
  Remove the application inventory reader over the raw Storage namespace.
- Read progress JSONL incrementally by byte offset, retaining partial writes for
  the next poll. Live output is separate from the bounded trace-summary channel.
- Update docstrings, runtime readiness checks, Guide help and documentation.

### Fixed

- Stop dropping model chunks before they reach the Rust frontend. An HTTP worker
  mesh no longer disables live output from the locally invoked Architect.
- Keep worker/step previews separate, replace final text without duplication and
  retain valid UTF-8 when bounding previews or reading partial progress lines.
- Mask configured secrets split across live output chunks and strip terminal
  controls from presentation. Unknown secrets still require application handling.
- Preserve failed, uncertain and input-required labels in the TUI.

### Boundaries

- JSON-action models can show raw JSON generation fragments. These previews are
  provisional; actions remain assembled, authorized and executed by ProtoLink.
- `PROTOAGENT_STREAM=0` hides previews without changing execution or replaying work.
- Existing approvals, budgets, POSIX recovery constraints, legacy inspection and
  bounded repairs remain. Native inventory includes original recovery bytes;
  ProtoAgent exposes metadata only and keeps recovery storage private.

## [0.2.2] - 2026-09-12

### Changed

- Require ProtoLink **0.7.0** across the core, CI and installation guidance;
  align active CLI/core/docs versions to 0.2.2.
- Register native `process_tool()` on Verifier as `execute_command`. Remove the
  custom subprocess runner, output drains, timeout and process cleanup code.
  Commands use explicit argv, cwd, env and limits; the environment is no longer
  inherited. Keep 600-second and 32-KiB application ceilings and native budgets.
- Replace manual embedded startup/cleanup and final-event reconstruction with
  `AgentGroup`, `RunHandle`, normalized `RunResult` and native reports.
- Use `ApprovalBroker` as the actual handler. Rust previews native JSON command
  specifications and filesystem diffs, echoes the exact request ID/fingerprint,
  and uses private temporary controls. Application authorization supplies scope.
- Register native create/replace/preview/restore filesystem tools with a dedicated
  `StorageCheckpointStore`, private SQLite storage and one writer per project.
  Remove custom diff, hash, file mutation and checkpoint execution helpers.
- Define completion with native `CompletionCheck`/`CompletionValidator` over
  executed outcomes and current resource revisions. Enforce an initial attempt
  and at most two repairs with native Graph limits, separate from transport retries.
- Update docstrings, Guide help, the operator manual and migration/API mapping.

### Fixed

- Approval, previews and delegation no longer count as a completed write.
- Quality evaluation counts executed native file edits; command approvals are
  not mistaken for Coder usage. Runtime diagnostics check the required 0.7 APIs.
- Preserve failed, canceled and uncertain results through the frontend adapter;
  errors after submission do not imply effects were absent.
- Reject stale preimages, restoration conflicts and further mutations after an
  uncertain checkpoint. Never automatically replay interrupted effects.
- Redact known credential values and recovery bytes from presentation/report
  copies while retaining protected native recovery and approval storage.

### Migration boundaries

- Recoverable Coder writes now require POSIX, absolute project paths without
  symlinks and existing parents. New files use native mode 0600. Directory
  creation requires a separately approved command and a later edit run.
- Preserve v0.2.1 checkpoint databases as inspection-only legacy records;
  they cannot establish native revision identities. Chained undo can conflict
  after a later restoration changes a file's identity.
- Local commands run on the host without sandbox isolation; recovery covers
  Coder file effects only. RunReplay remains inspection, not resumption.
- ProtoLink 0.7.0 delegation does not merge worker receipts into parent reports.
  Compose same-trace native RunStore snapshots for acceptance. Native snapshot
  redaction and checkpoint inventory still need small application adapters.

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
