"""RespondToWorkerTool — Orchestrator responds to a Worker's pending question."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema

if TYPE_CHECKING:
    from nanobot.agent.subagent import SubagentManager


@tool_parameters(
    build_parameters_schema(
        worker_id=p("string", "Label of the Worker to respond to (e.g. 'mod-a-analyzer')"),
        response=p("string", "Your response to the Worker's question"),
        required=["worker_id", "response"],
    )
)
class RespondToWorkerTool(Tool):
    """Orchestrator tool: respond to a Worker's pending request_orchestrator_input."""

    def __init__(self, manager: "SubagentManager") -> None:
        self._manager = manager

    name = "respond_to_worker"

    @property
    def description(self) -> str:
        return (
            "**用途**: 回复 Worker 的请求，解除其阻塞状态。\n\n"
            "当 Worker 调用 request_orchestrator_input 后，用此工具回复。\n"
            "Worker 收到回复后继续执行。\n\n"
            "**什么时候用**:\n"
            "- Worker 请求输入，需要你的决策\n"
            "- Worker 报告 blocker，你有解决方案\n\n"
            "**注意**: \n"
            "- worker_id 使用 Worker 的 label（不是 task_id）\n"
            "- 如果 Worker 已超时，回复会被忽略\n"
            "- 回复要清晰、可执行，让 Worker 能直接继续"
        )

    async def execute(self, worker_id: str, response: str, **kwargs: Any) -> str:
        return self._manager.respond_to_worker(worker_id=worker_id, response=response)
