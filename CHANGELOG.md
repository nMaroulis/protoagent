# Changelog

This file records user-visible changes to the active ProtoAgent components.

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
