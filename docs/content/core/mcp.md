---
title: MCP Broker
description: Model-free, lazy MCP tool discovery and approved invocation through ProtoLink 0.8.0.
---

ProtoAgent v0.3.0 uses ProtoLink 0.8.0's `MCPToolAdapter` for MCP tool access.
The optional `mcp` agent has **no LLM**. Architect calls its tools directly,
then gives a focused result to workers that need external evidence. Workers
keep their existing narrow tools and cannot start another delegation tree.

## Setup

MCP dependencies are included in the core install:

```bash
pip install -e core
# Or install the runtime dependency explicitly:
pip install "protolink[http,llms,mcp]>=0.8.0"
```

Create a server JSON file. For a local server, use an explicit executable and
absolute script paths. ProtoAgent does not install or guess server packages.

```json
{
  "transport": "stdio",
  "command": "/absolute/path/to/python",
  "args": ["/absolute/path/to/server.py"],
  "allow_tools": ["lookup_docs"],
  "read_only_tools": ["lookup_docs"]
}
```

For remote Streamable HTTP:

```json
{
  "transport": "streamable_http",
  "url": "https://your-server.example/mcp",
  "headers_env": {"Authorization": "DOCS_MCP_AUTH"},
  "allow_tools": ["lookup_docs"],
  "read_only_tools": ["lookup_docs"]
}
```

Set `DOCS_MCP_AUTH` in the launching shell to the complete header value, such as
`Bearer …`. Legacy `sse` is supported with its SSE endpoint. Raw headers,
credential-bearing/query URLs and arbitrary config keys are rejected.
The native adapter has no per-server stdio environment/cwd option in this
integration. Use a configured executable or wrapper when a server needs those.
Do not put secrets in command arguments or tool descriptions.

```bash
proto-cli mcp                         # Show status and setup guidance; no connection
proto-cli mcp add docs ./docs-mcp.json # Save config; no connection or automatic enable
proto-cli mcp test docs               # Explicitly connect and discover; invoke no tool
proto-cli mcp tools docs lookup_docs  # Inspect one exact input schema
proto-cli mcp tools docs --offset 12 # Next page if discovery returns next_offset
proto-cli mcp on                      # Enable broker on subsequent runs
proto-cli agents tester off           # Optional: skip the test-design model worker
proto-cli mcp off
proto-cli mcp remove docs
```

The TUI supports the same commands: `/mcp add docs "./docs MCP.json"`,
`/mcp test docs`, `/mcp tools docs lookup_docs`, `/mcp on|off`. `/agents mcp
on|off` changes the same toggle. These settings are user-wide and stored in
`~/.protoagent/config.json`, or the configured config directory, with mode 0600
where supported. Changes take effect on the next run, not during an active run.

To discover a server before selecting tools, omit `allow_tools` or use `[]`,
run `mcp test NAME`, then import the updated JSON with exact names. Discovery
is permitted, but no tool can execute until its name is allowlisted. There is
no wildcard. Up to eight named servers can be configured.

## Small-model tool surface

The registry advertises three broker tools and configured server names. The
complete remote catalog is never copied into every worker's initial prompt.

| Broker tool | Purpose |
| --- | --- |
| `list_mcp_tools(server, query="", offset=0)` | Filter names/descriptions; return up to 12 and a `next_offset`. |
| `mcp_tool_schema(server, tool)` | Return one original input schema and allowlist status. |
| `call_mcp_tool(server, tool, arguments)` | Validate a JSON argument object using ProtoLink's original MCP schema, then invoke exactly once. |

Architect uses `agent_call` with `agent="mcp"`, `action="tool_call"`,
`tool="call_mcp_tool"` and `args={"server":"docs","tool":"lookup_docs",
"arguments":{"query":"…"}}`. It reads the actual schema before choosing
arguments. The broker never runs an inference step. Identical tool names on
different servers remain distinct because `server` is explicit.

An oversized schema fails at 12,000 characters instead of losing constraints.
Results preserve native rich/structured content within a 6,000-character JSON
budget; larger results return a marked excerpt. Arguments have a 16,000-character
ceiling. These caps bound model handoffs after SDK decoding, not network transfer.
Configure tools that can return narrow results for small models.

Each operation uses `async with adapter.session()`: initialization, discovery
and an invocation share one session where applicable, and cleanup runs in the
same task on success, failure or cancellation. Separate operations reconnect;
stateful stdio servers should persist their own state externally. Calls have a
45-second operation timeout and are never automatically retried.

## Approvals and evidence

Runtime discovery uses `mcp.connect`; invocation uses `mcp.invoke`. Both require
native approval, including discovery that starts a subprocess. Previews show
the transport target, stdio arguments, operation and proposed tool arguments.
`mcp test/tools` are explicit user-requested connection probes and never call a
server tool. Disabled runtime MCP creates no broker or server connection.

`read_only_tools` is an owner's declaration, must be a subset of `allow_tools`
and does not skip approval. Explicitly read-only runs permit only those declared
tools. Invocation is denied after final verification begins or when earlier
file effects are uncertain. Native failures/denials stop automatic repairs.

Once an invocation begins, a tool error, interruption or session-cleanup failure
marks its external effects uncertain for the run. Further MCP invocations,
Coder writes/restores and Verifier commands are blocked until a new instruction
and run; evidence reads remain available. Argument validation happens before
invocation and does not by itself mark an external effect uncertain.

External content and schemas are untrusted. Server annotations do not establish
safe effects. MCP servers run on the host or remotely and may perform writes
outside ProtoAgent's file policy; approvals and allowlists are not sandboxing.
Checkpoint undo covers native Coder writes, not MCP effects. MCP receipts do not
qualify as local repository check results. Inspect effects before manually
retrying interrupted calls.

HTTP auth values referenced by `headers_env` join the runtime's secret redaction
set. Setup/status output shows references and metadata rather than credentials;
stdio arguments are hidden in config displays. Arbitrary secrets emitted by a
server may remain, just as with host command output.

This release supports MCP **tools**. Resources, prompts, OAuth login, server
installation and persistent cross-operation sessions are outside its scope.
