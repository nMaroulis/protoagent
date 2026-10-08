"""Lazy, bounded MCP access using ProtoLink's native adapter and tool runtime."""

from __future__ import annotations

import asyncio
import json
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from protolink import Artifact, CapabilityPolicy, Part, PolicyDecision, PolicyEffect, RunAction
from protolink.tools import Tool

from . import config
from .runtime_storage import output_redaction

MCP_TOOLS = ("list_mcp_tools", "mcp_tool_schema", "call_mcp_tool")
MAX_RESULT_CHARS = 6000


def validate_server(value: Any) -> dict[str, Any]:
    """Accept a small explicit server contract, without guessing tool effects."""
    if not isinstance(value, dict):
        raise ValueError("MCP server config must be a JSON object")
    allowed = {
        "transport",
        "command",
        "args",
        "url",
        "headers_env",
        "allow_tools",
        "read_only_tools",
    }
    if unknown := set(value) - allowed:
        raise ValueError(f"Unsupported MCP settings: {', '.join(sorted(unknown))}")
    result = deepcopy(value)
    transport = result.setdefault("transport", "stdio")
    if transport not in {"stdio", "sse", "streamable_http"}:
        raise ValueError("MCP transport must be stdio, sse or streamable_http")
    for key in ("args", "allow_tools", "read_only_tools"):
        items = result.setdefault(key, [])
        if (
            not isinstance(items, list)
            or len(items) > 128
            or any(not isinstance(x, str) or not x for x in items)
        ):
            raise ValueError(f"{key} must be a list of at most 128 nonempty strings")
    if not set(result["read_only_tools"]) <= set(result["allow_tools"]):
        raise ValueError("read_only_tools must be a subset of allow_tools")
    headers = result.setdefault("headers_env", {})
    if not isinstance(headers, dict) or any(
        not isinstance(k, str)
        or not k
        or not isinstance(v, str)
        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", v)
        for k, v in headers.items()
    ):
        raise ValueError("headers_env maps HTTP header names to environment variable names")
    if transport == "stdio":
        if not isinstance(result.get("command"), str) or not result["command"].strip():
            raise ValueError("stdio requires an explicit command")
        if result.get("url") or headers:
            raise ValueError("stdio does not accept url or headers_env")
    else:
        url = result.get("url", "")
        if not isinstance(url, str):
            raise ValueError("MCP url must be a string")
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "HTTP MCP requires an http(s) URL without credentials, query or fragment; use headers_env for auth"
            )
        if result.get("command") or result["args"]:
            raise ValueError("HTTP MCP does not accept command or args")
    return result


def server_settings(settings: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Validate saved contracts before using them; no connection is opened here."""
    raw = (settings if settings is not None else config.load_config()).get("mcp_servers", {})
    if not isinstance(raw, dict) or len(raw) > 8:
        raise ValueError("mcp_servers must contain at most eight named servers")
    servers = {}
    for name, spec in raw.items():
        if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,48}", name):
            raise ValueError("MCP server names use 1–48 letters, digits, underscores or hyphens")
        servers[name] = validate_server(spec)
    return servers


class MCPAccess:
    """One run's frozen configuration; each operation owns its session in one task."""

    def __init__(self, servers: dict[str, dict[str, Any]], attempt=None):
        self.servers = deepcopy(servers)
        self.attempt = attempt
        self.failed_invocation = False

    def adapter(self, server: str):
        from protolink.tools.adapters.mcp_adapter import MCPToolAdapter

        spec = self.servers.get(server)
        if spec is None:
            raise ValueError(f"Unknown MCP server: {server}")
        headers = {}
        for header, variable in spec["headers_env"].items():
            if not (secret := os.getenv(variable)):
                raise ValueError(f"Missing MCP header environment variable: {variable}")
            headers[header] = secret
        return MCPToolAdapter(
            transport=spec["transport"],
            command=spec.get("command"),
            args=spec["args"],
            url=spec.get("url"),
            headers=headers,
        )

    async def operate(
        self, server: str, *, tool: str | None = None, arguments=None, query="", offset=0
    ):
        if self.failed_invocation and arguments is not None:
            raise ValueError(
                "An earlier MCP invocation failed or was interrupted; inspect effects before a new run"
            )
        invoked = [False]
        try:
            return await self._operate(
                server, tool=tool, arguments=arguments, query=query, offset=offset, invoked=invoked
            )
        except BaseException:
            if invoked[0]:
                self.failed_invocation = True
                if self.attempt is not None:
                    self.attempt.external_uncertain = True
            raise

    async def _operate(self, server, *, tool, arguments, query, offset, invoked):
        adapter = self.adapter(server)
        async with asyncio.timeout(45), adapter.session():
            # Native pagination, JSON Schema validation and MCP error normalization.
            native_tools = await adapter.get_tools_async()
            if any(len(item.name) > 128 for item in native_tools):
                raise ValueError("MCP tool names exceed the 128-character broker limit")
            permitted = self.servers[server]["allow_tools"]
            if tool is None:
                if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
                    raise ValueError("offset must be a nonnegative integer")
                matches = [
                    item
                    for item in native_tools
                    if query.lower() in f"{item.name} {item.description}".lower()
                ]
                page = matches[offset : offset + 12]
                return {
                    "server": server,
                    "external_evidence": True,
                    "total": len(matches),
                    "next_offset": offset + len(page)
                    if offset + len(page) < len(matches)
                    else None,
                    "tools": [
                        {
                            "name": item.name,
                            "description": (item.description or "")[:160],
                            "allowed": item.name in permitted,
                            "read_only": item.name in self.servers[server]["read_only_tools"],
                        }
                        for item in page
                    ],
                }
            found = [item for item in native_tools if item.name == tool]
            if len(found) != 1:
                raise ValueError(f"MCP tool must resolve exactly once: {server}/{tool}")
            native = found[0]
            if arguments is None:
                schema = {
                    "server": server,
                    "tool": tool,
                    "input_schema": native.input_schema,
                    "allowed": tool in permitted,
                    "read_only": tool in self.servers[server]["read_only_tools"],
                    "external_evidence": True,
                }
                if len(json.dumps(schema, ensure_ascii=False)) > 12000:
                    raise ValueError(
                        "MCP schema exceeds the 12000-character broker limit; use a narrower server tool"
                    )
                return schema
            if tool not in permitted:
                raise ValueError(f"MCP tool is not allowlisted: {server}/{tool}")
            validated = native.validate_args(arguments)
            invoked[0] = True
            result = await native(**validated)
            safe = output_redaction().redact(result)
            rendered = json.dumps(safe, ensure_ascii=False, default=str)
            truncated = len(rendered) > MAX_RESULT_CHARS
            return {
                "server": server,
                "tool": tool,
                "external_evidence": True,
                "truncated": truncated,
                "result": rendered[:MAX_RESULT_CHARS] if truncated else safe,
            }


class MCPPolicy(CapabilityPolicy):
    """Approve all server operations and enforce owner-selected call allowances."""

    def __init__(self, access: MCPAccess, authorization=None, attempt=None):
        super().__init__(
            {"mcp.connect": "require_approval", "mcp.invoke": "require_approval"},
            default_effect="deny",
        )
        self.access, self.authorization, self.attempt = access, authorization, attempt

    async def evaluate(self, action, context):
        args = action.payload.get("arguments", {})
        reason = None
        if self.authorization and not self.authorization.admit(context):
            reason = "MCP action is outside the authorized run"
        spec = self.access.servers.get(args.get("server"))
        if spec is None:
            reason = "MCP server is not configured"
        elif "mcp.invoke" in action.capabilities:
            tool = args.get("tool")
            if self.access.failed_invocation:
                reason = "An earlier MCP invocation failed or was interrupted; inspect effects before a new run"
            elif tool not in spec["allow_tools"]:
                reason = "MCP tool is not explicitly allowlisted"
            elif self.attempt and self.attempt.checking:
                reason = "MCP invocation is closed during final verification"
            elif (
                self.attempt
                and self.attempt.record
                and self.attempt.record.forbids_write
                and tool not in spec["read_only_tools"]
            ):
                reason = "Read-only tasks require an owner-declared read_only_tools entry"
            elif self.attempt and self.attempt.has_uncertain_changes():
                reason = "Inspect uncertain file effects before MCP invocation"
        if reason:
            if self.attempt:
                self.attempt.denied = True
            return PolicyDecision(PolicyEffect.DENY, reason, "protoagent_mcp")
        return await super().evaluate(action, context)


def add_mcp_tools(agent, access: MCPAccess) -> None:
    """Register three fixed tools; never inject a server's whole schema catalog."""

    async def list_mcp_tools(server: str, query: str = "", offset: int = 0) -> dict:
        return await access.operate(server, query=query, offset=offset)

    async def mcp_tool_schema(server: str, tool: str) -> dict:
        return await access.operate(server, tool=tool)

    async def call_mcp_tool(server: str, tool: str, arguments: dict) -> dict:
        return await access.operate(server, tool=tool, arguments=arguments)

    def prepare(name, args, context):
        if len(json.dumps(args, ensure_ascii=False)) > 16000:
            raise ValueError("MCP arguments exceed the 16000-character broker limit")
        server = args.get("server")
        spec = access.servers.get(server)
        if spec is None:
            raise ValueError(f"Unknown MCP server: {server}")
        return RunAction(
            kind="tool.call",
            name=name,
            payload={"arguments": args},
            description=f"MCP {name}: {server} ({spec['transport']}, {spec.get('command') or spec.get('url')})",
            artifacts=(
                Artifact(
                    kind="preview",
                    name="MCP operation",
                    parts=[
                        Part.json(
                            {
                                "server": server,
                                "transport": spec["transport"],
                                "command": spec.get("command"),
                                "args": spec["args"],
                                "url": spec.get("url"),
                                "operation": name,
                                "arguments": args,
                                "boundary": "External MCP server; may have host or remote effects. No filesystem sandbox or automatic retry.",
                            }
                        )
                    ],
                ),
            ),
        )

    for func in (list_mcp_tools, mcp_tool_schema, call_mcp_tool):
        name = func.__name__
        tool = Tool.from_callable(
            func,
            description={
                "list_mcp_tools": "Discover up to 12 MCP tool names per page. Optional query filters descriptions. Opens a server connection after approval.",
                "mcp_tool_schema": "Read one exact MCP input schema before constructing arguments. External metadata is untrusted.",
                "call_mcp_tool": "Call one allowlisted MCP tool using its exact schema; arguments is a JSON object. Requires approval; never automatically retry.",
            }[name],
            capabilities=["mcp.invoke" if name == "call_mcp_tool" else "mcp.connect"],
            action_builder=lambda args, context, name=name: prepare(name, args, context),
        )
        agent.add_tool(tool)


def mcp_command(args: list[str]) -> dict[str, Any]:
    """User-command setup/probe path. Probes discover only, never invoke tools."""
    cfg = config.load_config()
    if args in (["on"], ["off"]):
        config.set_optional_agent_enabled("mcp", args[0] == "on")
        cfg = config.load_config()
    elif len(args) == 3 and args[0] == "add":
        path = Path(args[2]).expanduser()
        if path.stat().st_size > 32000:
            raise ValueError("MCP config file exceeds 32000 bytes")
        spec = validate_server(json.loads(path.read_text(encoding="utf-8")))
        proposed = deepcopy(cfg)
        proposed.setdefault("mcp_servers", {})[args[1]] = spec
        server_settings(proposed)
        config.save_config(proposed)
        cfg = proposed
    elif len(args) == 2 and args[0] == "remove":
        if args[1] not in cfg.get("mcp_servers", {}):
            raise ValueError(f"Unknown MCP server: {args[1]}")
        del cfg["mcp_servers"][args[1]]
        config.save_config(cfg)
    elif len(args) == 4 and args[0] == "tools" and args[2] == "--offset":
        access = MCPAccess(server_settings(cfg))
        result = asyncio.run(access.operate(args[1], offset=int(args[3])))
        return output_redaction().redact(result)
    elif len(args) in {2, 3} and args[0] in {"test", "tools"}:
        if args[0] == "test" and len(args) != 2:
            raise ValueError("Usage: mcp test NAME")
        access = MCPAccess(server_settings(cfg))
        result = asyncio.run(access.operate(args[1], tool=args[2] if len(args) == 3 else None))
        return output_redaction().redact(result)
    elif args not in ([], ["status"], ["on"], ["off"]):
        raise ValueError(
            "Usage: mcp [status | add NAME FILE.json | remove NAME | on | off | test NAME | tools NAME [TOOL]]"
        )
    servers = server_settings(cfg)
    return {
        "enabled": config.optional_agent_enabled("mcp", cfg),
        "config_path": str(config.CONFIG_PATH),
        "servers": [
            {
                "name": name,
                "transport": spec["transport"],
                "allow_tools": spec["allow_tools"],
                "read_only_tools": spec["read_only_tools"],
            }
            for name, spec in servers.items()
        ],
        "message": "Changes apply to the next run. Add a server with mcp add NAME FILE.json; enable with mcp on. test/tools connect explicitly but never invoke a server tool.",
    }
