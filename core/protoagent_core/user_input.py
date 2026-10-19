"""Controller configuration for ProtoLink's native user-question tool."""

import re

from protolink.tools.builtins import ask_user_tool
from protolink.tools.prepared import PreparedTool

MAX_ANSWER_CHARS = 4096


def hands_off_verification(question: str, answer: str) -> bool:
    """Recognize an explicit stop/handoff in a verification-related answer."""
    context = bool(
        re.search(
            r"\b(?:tests?|testing|verify|verification|checks?|dependency|dependencies)\b",
            question,
            re.I,
        )
    )
    text = answer.casefold().replace("’", "'")
    negative = re.search(
        r"\b(?:do not|don't|dont)\s+(?:re[- ]?run|run|execute|test|verify)\b|\b(?:skip|stop)\s+(?:the\s+)?(?:tests?|testing|verification|checks?)\b",
        text,
    )
    manual = re.search(
        r"\b(?:i'll|i will|ill)\s+(?:run|verify|test|check)\b[^.;]*\b(?:myself|manually)",
        text,
    )
    # Do not misread "don't run tests yet; first install X" as a handoff.
    other_action = re.search(
        r"(?:\b(?:but|instead|first|then|and|please)\b|[,;.])\s+(?:please\s+)?(?:install|run|execute|fix|change|verify|test|read|review|explain|update|create|add|make)\b",
        text,
    )
    return context and bool(negative or manual) and not other_action


async def decline_input(request):
    """Headless controllers have no user answer to supply."""
    return None


def add_user_input_tool(agent, handler=None, record=None) -> None:
    """Attach the engine-owned tool; applications supply only its UI callback."""
    callback = handler or decline_input
    native = ask_user_tool(callback, max_answer_chars=MAX_ANSWER_CHARS)
    if record is not None:
        execute_native = native.execute_authorized

        async def execute(execution):
            result = await execute_native(execution)
            question = execution.authorization.action.payload["arguments"]["question"]
            if result.status == "answered":
                record.clarifications[result.request_id] = {
                    "question": question,
                    "answer": result.answer,
                }
            if (
                record.frozen
                and result.status == "answered"
                and hands_off_verification(question, result.answer)
            ):
                choice = {
                    "request_id": result.request_id,
                    "question": question,
                    "answer": result.answer,
                }
                record.clarifications[result.request_id] = {
                    "question": question,
                    "answer": result.answer,
                }
                record.verification_handoff = choice
                await execution.emit(
                    "verification.deferred",
                    summary="User chose to handle verification; stopping further execution.",
                    choice=choice,
                )
                if execution.cancellation is not None:
                    execution.cancellation.cancel("User chose manual verification")
                else:
                    execution.context.cancel("User chose manual verification")
            return result

        assert native.action_builder is not None
        native = PreparedTool(
            native.func,
            prepare=native.action_builder,
            execute=execute,
            capabilities=tuple(native.capabilities or ()),
            name=native.name,
        )
        agent.execution_version += ":protoagent-verification-handoff-1"
    agent.add_tool(native)
