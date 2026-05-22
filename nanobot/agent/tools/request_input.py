"""RequestOrchestratorInputTool — blocking Worker → Orchestrator question."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema

if TYPE_CHECKING:
    from nanobot.agent.subagent import SubagentManager


@tool_parameters(
    build_parameters_schema(
        question=p("string", "The question for the Orchestrator"),
        context=p("string", "Optional context to help the Orchestrator understand the question"),
        timeout=p("number", "Seconds to wait before continuing autonomously (default 300)"),
        required=["question"],
    )
)
class RequestOrchestratorInputTool(Tool):
    """Blocking: Worker asks Orchestrator for input, pauses until response or timeout."""

    def __init__(self, manager: "SubagentManager", worker_id: str, worker_label: str) -> None:
        self._manager = manager
        self._worker_id = worker_id
        self._worker_label = worker_label

    name = "request_orchestrator_input"

    @property
    def description(self) -> str:
        return (
            "**用途**: 向 Orchestrator 请求输入，阻塞等待回复。\n\n"
            "当你需要 Orchestrator 的决策或信息才能继续时使用。\n"
            "调用后暂停执行，不消耗迭代预算。\n\n"
            "**什么时候用**:\n"
            "- 需要确认方向是否正确\n"
            "- 遇到了需要全局视角才能做的决策\n"
            "- 需要其他 Worker 产出的信息\n\n"
            "**什么时候不用**:\n"
            "- 只是一般性通知 → 用 notify_orchestrator\n"
            "- 可以跳过或绕过的问题 → 自己决策，不要阻塞\n\n"
            "**注意**: Orchestrator 回复前会暂停执行。"
            "超时（默认 300s）后自动继续自主执行。"
        )

    async def execute(self, question: str, context: str = "", timeout: float = 300.0, **kwargs: Any) -> str:
        return await self._manager.request_orchestrator_input(
            question=question,
            worker_id=self._worker_id,
            worker_label=self._worker_label,
            context=context,
            timeout=timeout,
        )
