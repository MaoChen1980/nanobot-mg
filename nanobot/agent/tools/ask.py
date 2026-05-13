"""Tool for pausing a turn until the user answers."""

from __future__ import annotations

import json
from typing import Any

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p

STRUCTURED_BUTTON_CHANNELS = frozenset({"telegram", "websocket"})


class AskUserInterrupt(BaseException):
    """Internal signal: the runner should stop and wait for user input."""

    def __init__(self, question: str, options: list[str] | None = None) -> None:
        self.question = question
        self.options = [str(option) for option in (options or []) if str(option)]
        super().__init__(question)


@tool_parameters(properties={
    "question": p("string", "The question to ask before continuing. Use this only when the task needs the user's answer."),
    "options": p("array", "Optional choices. The user may still reply with free text.", items=p("string", "A possible answer label")),
}, required=["question"])
class AskUserTool(Tool):
    """Ask the user a blocking question."""

    name = "ask_user"

    description = (
        "**用途**: 阻塞式提问 — 暂停执行直到用户回答。\n\n"
        "**限制**:\n"
        "- 会暂停当前任务，必须等用户回复\n"
        "- options 只是建议，用户仍可自由输入\n\n"
        "**错误应对**:\n"
        "- 用户不回复 → 任务一直暂停\n\n"
        "**边界条件**:\n"
        "- 只是通知用户 → 用 message\n"
        "- 可以合理默认值继续 → 不问，直接做\n\n"
        "**极简案例**: ask_user(question='继续吗？', options=['是','否'])\n"
        "→ 暂停等待用户选择"
    )

    exclusive = True

    async def execute(self, question: str, options: list[str] | None = None, **_: Any) -> Any:
        raise AskUserInterrupt(question=question, options=options)


def _tool_call_name(tool_call: dict[str, Any]) -> str:
    function = tool_call.get("function")
    if isinstance(function, dict) and isinstance(function.get("name"), str):
        return function["name"]
    name = tool_call.get("name")
    return name if isinstance(name, str) else ""


def _tool_call_arguments(tool_call: dict[str, Any]) -> dict[str, Any]:
    function = tool_call.get("function")
    raw = function.get("arguments") if isinstance(function, dict) else tool_call.get("arguments")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def pending_ask_user_id(history: list[dict[str, Any]]) -> str | None:
    pending: dict[str, str] = {}
    for message in history:
        if message.get("role") == "assistant":
            for tool_call in message.get("tool_calls") or []:
                if isinstance(tool_call, dict) and isinstance(tool_call.get("id"), str):
                    pending[tool_call["id"]] = _tool_call_name(tool_call)
        elif message.get("role") == "tool":
            tool_call_id = message.get("tool_call_id")
            if isinstance(tool_call_id, str):
                pending.pop(tool_call_id, None)
    for tool_call_id, name in reversed(pending.items()):
        if name == "ask_user":
            return tool_call_id
    return None


def ask_user_tool_result_messages(
    system_prompt: str,
    history: list[dict[str, Any]],
    tool_call_id: str,
    content: str,
) -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": system_prompt},
        *history,
        {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": "ask_user",
            "content": content,
        },
    ]


def ask_user_options_from_messages(messages: list[dict[str, Any]]) -> list[str]:
    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        for tool_call in reversed(message.get("tool_calls") or []):
            if not isinstance(tool_call, dict) or _tool_call_name(tool_call) != "ask_user":
                continue
            options = _tool_call_arguments(tool_call).get("options")
            if isinstance(options, list):
                return [str(option) for option in options if isinstance(option, str)]
    return []


def ask_user_outbound(
    content: str | None,
    options: list[str],
    channel: str,
) -> tuple[str | None, list[list[str]]]:
    if not options:
        return content, []
    if channel in STRUCTURED_BUTTON_CHANNELS:
        return content, [options]
    option_text = "\n".join(f"{index}. {option}" for index, option in enumerate(options, 1))
    return f"{content}\n\n{option_text}" if content else option_text, []
