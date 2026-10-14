"""Typed worker calls backed by ProtoLink's owned child supervisor."""

from __future__ import annotations

from copy import deepcopy

from protolink import RunAction, ToolRejectedError
from protolink.agents.subagents import current_subagent_supervisor
from protolink.llms.delegation import validate_delegation_request
from protolink.tools.prepared import PreparedTool

WORKER_TOOLS = {
    "explorer": ("read_file", "list_directory", "search_regex"),
    "verifier": ("run_check", "execute_command"),
    "scout": ("web_search", "fetch_url"),
    "mcp": ("list_mcp_tools", "mcp_tool_schema", "call_mcp_tool"),
}


def install_worker_interface(agent) -> None:
    """Register a fixed, typed route per operation; never interpret invented names.

    Each prepared call authorizes agent.delegate and reaches the original child
    through native supervision. The child validates and authorizes its own tools,
    shares budgets/policies, and contributes native receipts to the parent run.
    No tool implementation is copied onto Architect and no child runs here.
    """
    if not agent.subagents:
        raise ValueError("The simplified worker interface requires owned local children")
    routes = {}

    def register(worker, name, signature, action, target_tool=None):
        if name in agent.tools:
            raise ValueError(f"Worker interface tool collides with local tool: {name}")

        def prepare(arguments, context):
            payload = (
                {"action": "infer", "prompt": arguments["task"]}
                if action == "infer"
                else {"action": "tool_call", "tool": target_tool, "args": arguments}
            )
            payload = validate_delegation_request(
                worker.card.name, action, payload, [worker.card], agent_chain=context.agent_chain
            )
            return RunAction(
                kind="agent.call",
                name=worker.card.name,
                payload={**payload, "arguments": arguments},
                capabilities=frozenset({"agent.delegate"}),
                metadata={"worker_interface": name},
            )

        async def execute(execution):
            execution.check()
            supervisor = current_subagent_supervisor()
            if supervisor is None or supervisor.agent is not agent:
                raise RuntimeError("Worker calls require this Architect's active native supervisor")
            prepared = execution.authorization.action
            if worker.card.name == "verifier" and target_tool in {"execute_command", "run_check"}:
                # These native builders only inspect/resolve a command. Validate
                # before child dispatch so preparation mistakes reach the
                # controller's native correction loop, rather than becoming a
                # failed delegated execution. The child still prepares and
                # authorizes its exact command; no process runs here.
                try:
                    worker.tools[target_tool].action_builder(
                        prepared.payload["args"], execution.context
                    )
                except ToolRejectedError:
                    raise
                except (ValueError, OSError) as error:
                    raise ToolRejectedError(
                        str(error),
                        code="invalid_command_preparation",
                        details={"tool": target_tool, "cwd_base": "project root"},
                        suggestion="No child or command was executed. Use an existing project-relative cwd, an installed executable (or explicit env.PATH), timeout_seconds <= 600 and max_output_bytes <= 32768. Use run_check with a selected ID for final verification.",
                    ) from error
            return await supervisor.call(
                worker.card.name, action, prepared.payload, prepared.action_id
            )

        tool = PreparedTool(
            signature,
            name=name,
            prepare=prepare,
            execute=execute,
            capabilities=("agent.delegate",),
        )
        if target_tool:
            original = worker.tools[target_tool]
            tool.input_schema = deepcopy(original.input_schema)
            tool.description = f"Call {worker.card.name}: {original.description}"
            if name == "run_check":
                tool.input_schema["properties"]["phase"]["enum"] = ["baseline", "verify"]
        else:
            if tool.input_schema is None:
                raise ValueError("Worker inference requires a typed input schema")
            tool.input_schema["properties"]["task"].update(minLength=1, pattern=r"\S")
            tool.description = (
                "Apply the requested edits and regression tests in the task's file scope. "
                "This call runs Coder now; include concrete paths and the required behavior."
                if name == "coder"
                else "Ask Tester for focused regression criteria using source and existing tests. No edits or commands."
            )
        agent.add_tool(tool)
        routes[name] = {"worker": worker.card.name, "action": action, "tool": target_tool}

    def infer(task: str) -> dict:
        """Run a focused worker task."""
        raise AssertionError("Signature only")

    for name, worker in agent.subagents.items():
        if name in {"coder", "tester"} and worker.card.capabilities.supports_inference:
            register(worker, name, infer, "infer")
        for tool_name in WORKER_TOOLS.get(name, ()):
            if tool_name in worker.tools:
                register(worker, tool_name, worker.tools[tool_name].func, "tool_call", tool_name)
    agent.protoagent_worker_interface = routes
    agent.execution_version += ":protoagent-worker-interface-2"
