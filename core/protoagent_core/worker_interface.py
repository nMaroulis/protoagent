"""Coding-role configuration for ProtoLink's native typed worker declarations."""

WORKER_TOOLS = {
    "explorer": ("read_file", "list_directory", "search_regex"),
    "verifier": ("run_check", "execute_command"),
    "scout": ("web_search", "fetch_url"),
    "mcp": ("list_mcp_tools", "mcp_tool_schema", "call_mcp_tool"),
}


def install_worker_interface(agent) -> None:
    """Select narrow SDK worker tools; the engine owns routing and execution."""
    if not agent.subagents:
        raise ValueError("The simplified worker interface requires owned local children")
    routes = {}
    for name, worker in agent.subagents.items():
        if name in {"coder", "tester"} and worker.card.capabilities.supports_inference:
            description = (
                "Apply the requested edits and regression tests in the task's file scope. "
                "This call runs Coder now; include concrete paths and the required behavior."
                if name == "coder"
                else "Ask Tester for focused regression criteria using source and existing tests. No edits or commands."
            )
            agent.add_worker_tool(name, name=name, description=description)
            routes[name] = {"worker": name, "action": "infer", "tool": None}
        for tool_name in WORKER_TOOLS.get(name, ()):
            if tool_name in worker.tools:
                facade = agent.add_worker_tool(name, tool=tool_name, name=tool_name)
                if tool_name == "run_check":
                    facade.input_schema["properties"]["phase"]["enum"] = ["baseline", "verify"]
                routes[tool_name] = {"worker": name, "action": "tool_call", "tool": tool_name}
    agent.protoagent_worker_interface = routes
    agent.execution_version += ":protoagent-worker-interface-3"
