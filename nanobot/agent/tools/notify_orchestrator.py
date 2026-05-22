"""NotifyOrchestratorTool — fire-and-forget Worker → Orchestrator notification."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema

if TYPE_CHECKING:
    from nanobot.agent.subagent import SubagentManager


@tool_parameters(
    build_parameters_schema(
        message=p("string", "The message to send to the Orchestrator"),
        priority=p("string", "Priority: info, suggestion, or blocker"),
        required=["message"],
    )
)
class NotifyOrchestratorTool(Tool):
    """Fire-and-forget: send a notification from Worker to Orchestrator."""

    def __init__(self, manager: "SubagentManager", worker_id: str, worker_label: str) -> None:
        self._manager = manager
        self._worker_id = worker_id
        self._worker_label = worker_label

    name = "notify_orchestrator"

    @property
    def description(self) -> str:
        return (
            "**用途**: 向 Orchestrator 发送通知（不阻塞）。\n\n"
            "当你发现更好的方案、踩坑、或需要报告进展时使用。\n"
            "Orchestrator 会在下一轮迭代中看到你的消息。\n\n"
            "**优先级**:\n"
            "- info: 一般信息、进展报告\n"
            "- suggestion: 改进建议（发现更好的方案）\n"
            "- blocker: 阻塞性问题，需要 Orchestrator 决策\n\n"
            "**注意**: 这是 fire-and-forget，调用后继续执行。\n"
            "如果需要 Orchestrator 回复，使用 request_orchestrator_input。"
        )

    async def execute(self, message: str, priority: str = "info", **kwargs: Any) -> str:
        if priority not in ("info", "suggestion", "blocker"):
            priority = "info"
        return self._manager.notify_orchestrator(
            message=message,
            worker_id=self._worker_id,
            worker_label=self._worker_label,
            priority=priority,
        )
