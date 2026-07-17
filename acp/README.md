# ProtoAgent ACP

Status: **planned** (`0.0.0-dev.0`).

This directory reserves the future Agent Client Protocol editor adapter. There
is currently no installable ACP package, server entrypoint, or supported editor
configuration in the repository. The active `0.2.0` release surfaces are the
Rust CLI and Python core.

The planned adapter should remain thin:

1. map editor requests to the same `protoagent-core` entrypoints used by the
   CLI;
2. run the same ProtoLink mesh rather than implement a second agent runtime;
3. preserve stable editor session IDs in ProtoLink state;
4. translate ProtoLink approval requests, diff artifacts, cancellation, and
   run events into editor-native interactions;
5. expose the core Architect/Explorer/Coder deck and optional Scout without
   changing their capability boundaries.

No ACP setup instructions should be published until the server, protocol
mapping, tests, and at least one editor integration exist.

See the [ACP status page](https://nmaroulis.github.io/protoagent/docs/acp/overview)
and [implementation plan](https://nmaroulis.github.io/protoagent/docs/acp/plan).
