"""Controller configuration for ProtoLink's native user-question tool."""

from protolink import AgentHooks
from protolink.tools.builtins import ask_user_tool
from protolink.tools.builtins.user_input import UserInputResult

MAX_ANSWER_CHARS = 4096


async def decline_input(request):
    """Headless controllers have no user answer to supply."""
    return None


def add_user_input_tool(agent, handler=None, record=None) -> None:
    """Attach the engine-owned tool; applications supply only its UI callback."""
    callback = handler or decline_input
    if record is not None:
        questions = {}

        async def receive(request):
            questions[request.request_id] = request.question
            return await callback(request)

        def retain(observation):
            if observation.name != "ask_user":
                return
            result = observation.result
            if isinstance(result, UserInputResult):
                result = result.to_dict()
            if not isinstance(result, dict):
                return
            request_id = result.get("request_id")
            question = questions.pop(request_id, None)
            if question is not None and result.get("status") == "answered":
                # Native execution already validated this outcome. Application
                # obligations survive observation pruning; receipts are untouched.
                record.clarifications[request_id] = {
                    "question": question,
                    "answer": result["answer"],
                }

        agent.hooks = (*agent.hooks, AgentHooks(after_tool=retain))
        callback_to_install = receive
    else:
        callback_to_install = callback
    agent.add_tool(ask_user_tool(callback_to_install, max_answer_chars=MAX_ANSWER_CHARS))
