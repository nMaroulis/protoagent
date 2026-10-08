"""Optional MCP broker: fixed discovery/call surface, no inference loop."""

from protolink import Agent

from ..mcp import MCPAccess, MCPPolicy, add_mcp_tools
from .common import QUIET_LOGGER, create_configured_transport, resolve_agent_url


def create_mcp_agent(
    *,
    servers,
    registry=None,
    url=None,
    transport="sse",
    approval_handler=None,
    telemetry=None,
    authenticator=None,
    credentials=None,
    authorization=None,
    attempt=None,
):
    access = MCPAccess(servers, attempt=attempt)
    agent_url = resolve_agent_url("mcp", url)
    agent = Agent(
        card={
            "name": "mcp",
            "url": agent_url,
            "description": f"Tool-only MCP broker. Configured servers: {', '.join(servers) or '(none)'}. Use list_mcp_tools, then mcp_tool_schema, then call_mcp_tool; never infer.",
            "capabilities": {
                "streaming": True,
                "delegation": False,
                "tool_calling": True,
                "multi_step_reasoning": False,
            },
            "tags": ["protoagent", "mcp", "optional"],
        },
        transport=create_configured_transport(
            transport, agent_url, authenticator=authenticator, credentials=credentials
        ),
        registry=registry,
        llm=None,
        storage=None,
        state=[],
        expose_chat=False,
        authenticator=authenticator,
        credentials=credentials,
        telemetry=telemetry,
        approval_handler=approval_handler,
        policy=MCPPolicy(access, authorization, attempt),
        logger=QUIET_LOGGER,
        verbosity=0,
    )
    add_mcp_tools(agent, access)
    return agent
