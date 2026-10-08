# Changelog

This file records user-visible changes to the active ProtoAgent components.

## [0.3.0] - 2026-09-26

### Added

- Runtime-owned TaskRecord with criteria, check selection, scoped worker packets
  and explicit done/needs_context/blocked worker reports.
- Stateless, read-only Tester for regression planning and failure analysis.
- Persistent optional-agent controls for Tester (default on), Scout and MCP
  (default off). Disabled workers are not constructed or registered; required
  Architect/Explorer/Coder/Verifier roles cannot be disabled.
- `mcp` and `/mcp` setup, explicit discovery probes, server/tool allowlists and
  environment-referenced HTTP authentication. Model-free MCP broker with three
  fixed discovery/schema/call tools, native approvals and bounded model handoffs.
- Repository check discovery, `.protoagent/project.json` configuration, and
  Verifier `run_check(check_id, phase)` with baseline and final verification.
- Coder bounded source reads and exact single-occurrence `edit_file` with a
  source revision guard, native diff approval and native recovery.
- Compact small-profile protocol prompts and worker cards with exact input
  schemas, omitting repetitive examples and large output schemas.
- Per-request context admission through native ProtoLink policies and hooks,
  reserving output tokens and retaining the current task and runtime record.
  Small profiles with unknown capacity receive an 8192-token application cap.
- Disposable coding evals with independent acceptance scripts and a same-model
  single-agent baseline: `proto-cli eval coding --plan|--live`.
- Owned local subagents through ProtoLink 0.8: independent conversations,
  inherited policies, shared Graph budgets, sequential dispatch, depth one and
  configurable per-attempt child limits. Default execution needs no Registry
  server or loopback sockets; explicit transport meshes remain available.
- Native ContextPolicy and AgentHooks for current task state, complete-turn
  pruning and scoped progressive retrieval of large tool/child observations.
- Optional explicit same-provider `fallback_models` through native RoutedLLM,
  with bounded transient request fallback, shared accounting and no tool replay.
- `eval harness [--json]`: native evaluate() runs repeated offline application
  read/task-state cases with fresh factories and linked child receipt checks.
- Architect-only native `ask_user` for clarification and same-task continuation.
  TUI/shell question input supports explicit suggestions, free text, skip and
  cancellation, correlated replies, native deadlines and no implicit approval.

### Changed

- Python projects without repository checks receive a predefined unittest
  runner for new root regression tests. Compact task records expose valid and
  bootstrap check IDs; empty suites still cannot verify changes. Planning
  mistakes return corrective feedback without changing the plan, and omitted
  check IDs preserve defaults. Explicit project configuration stays authoritative.

- Ollama models advertising `tools` use ProtoLink's native tool channel, with
  bounded cached metadata discovery and explicit auto/native/json overrides.
  Small JSON prompts include an Explorer README delegation example. A native
  completion hook rejects unfinished action-shaped answers before completion.
  Earlier malformed answers become invalid-answer notes in model context while
  valid historical actions and original reports remain intact.

- Code changes require selected repository checks executed at current revisions.
  Preparation and unrelated successful commands do not satisfy verification.
  Applied, verified and criteria-supported states are reported separately.
- Baseline and preparation commands allow subsequent edits; final checks close
  the edit phase and preserve the two-repair ceiling.
- Source tools return one bounded source span with line range and SHA-256 revision;
  the duplicated `raw_content` field is removed.
- Explicit small-model hints take priority over provider hosting location.
- Intent classification handles polite requests and explicit no-write instructions.
- CLI/core/docs coordinated versions advance to 0.3.0; ProtoLink floor advances
  to 0.8.0 with the HTTP, LLM and MCP extras.
- Replace model acquisition wrappers with native context policies and lifecycle
  hooks. The small profile retains compact declarations and exact input schemas.
- Present local child output from native task records before join, deduplicated
  against the parent stream, preserving live model/process previews.

### Migration and limits

- Existing code-change runs without a repository check now remain incomplete.
  Configure explicit argv, cwd, env and dependency paths for uncommon projects.
- `execute_command` remains approved host execution; only frozen repository checks
  qualify as verification. Passing checks supports criteria, not arbitrary semantics.
- Tester is enabled by default and can be disabled with `agents tester off`.
  Architect/Coder then handle criteria and regressions; required checks remain.
- MCP tool access is optional and off by default. Calls use managed ProtoLink
  sessions without automatic retries. Failed started invocations mark external
  effects uncertain and block further effectful work in the run.
  Approvals/allowlists do not sandbox server
  effects; remote receipts cannot satisfy repository verification. Resources,
  prompts, OAuth and server installation are not part of this integration.
- Request admission uses token estimates. Runtime-held task state survives observation
  eviction within a live run. Live model performance remains to be measured with coding evals.
- Native execution restart/resume, Docker check execution and background model
  supervision are not integrated into the custom coding Graph. Saved conversations,
  reports and file recovery are distinct from execution continuation.

## [0.2.3] - 2026-09-15

### Added

- Live model generation and delegated command stdout/stderr in shell and TUI,
  using native `RunHandle.events()` and explicit streaming Agent capabilities.
  Answers stream under a consistent `AGENT / architect` or `AGENT / guide`
  heading with a cyan prompt and steady mint `_` cursor. Runtime activity
  stays in the status area; `/trace` exposes details and up to four retained
  worker/process previews of 4096 characters. Answers stay complete.
- Streaming Guide help in `/help QUESTION` and `proto-cli help "QUESTION"`,
  with native cancellation and no project or saved conversation required.
- Packaged command reference shared by Guide and TUI command completion,
  including `/config`, shell equivalents, settings commands and aliases.
- `/debug [on|off]` reveals or hides response metadata and a `/trace` hint,
  including on existing answers. It defaults off for each TUI session.
- Animated three-dot thinking, faint hints inside the empty composer, and a
  slow cursor blink (700 ms per phase) that does not repaint the chat.
- Two muted composer borders and a lower single-line prompt that expands upward
  for multiline input; command suggestions sit in the lower border.
- Markdown styling during generation for Architect and Guide answers: bold,
  italic, headings, highlighted inline code and indented fenced code blocks.
  Formatting survives wrapped lines and partial delimiters; stored answers
  retain their original Markdown.
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
- Cache immutable message layouts and repaint only changed transcript rows during
  streaming. Animation ticks retain the answer without copying or reformatting
  it; resize, debug and modal transitions refresh the relevant layout.
- Bound live TUI ingestion to 256 records or 256 KiB checked between records per
  poll, retain the latest 512 status summaries, and drain remaining records at
  completion. Native traces and complete answers remain intact.
- Update docstrings, runtime readiness checks, Guide help and documentation.

### Fixed

- Stop dropping model chunks before they reach the Rust frontend. An HTTP worker
  mesh no longer disables live output from the locally invoked Architect.
- Keep worker/step previews separate, replace final text without duplication and
  retain valid UTF-8 when bounding previews or reading partial progress lines.
- Hide JSON-action envelopes during generation: progressively decode final
  answer text, including split escapes and Unicode, into the Architect message.
  Native-tool models can still stream genuine JSON answers unchanged. Trace
  details remain available through `/trace` without repeating the answer.
- Mask configured secrets split across live output chunks and strip terminal
  controls from presentation. Unknown secrets still require application handling.
- Preserve failed, uncertain and input-required labels in the TUI.
- Stop the answer jumping at completion: retain the same message layout and
  cursor cell, avoid inserting report footnotes or collapsing a trace above it,
  and synchronize terminal redraws where supported.
- Keep runtime activity only in the bottom status bar. Remove the duplicate
  top loading indicator and the bright input instructions beside the answer.
- Give the response cursor the same background as its text and stop its blink.
  Keep the terminal's default background for answers and the existing UI
  palette; limit Markdown color changes to response text and code highlights.
  Position styled spans by Unicode display width so emoji and wide characters
  do not overlap the next span.
- Give Guide explicit configuration guidance: `/config` and `proto-cli config`
  inspect redacted settings; model, key, context and agent commands change them.

### Boundaries

- Streamed answers are provisional until the native task establishes completion,
  failure, cancellation or uncertainty. Partial JSON is projected for display
  only; actions remain assembled, authorized and executed by ProtoLink.
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
