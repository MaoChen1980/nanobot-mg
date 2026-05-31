[Subagent '{{ label }}' {{ status_text }}]

Task: {{ task }}
{% if duration_s %}耗时：{{ "%.1f"|format(duration_s) }}s
{% endif %}{% if tools_used %}使用工具：{{ tools_used }}
{% endif %}{% if iteration_count %}迭代次数：{{ iteration_count }}
{% endif %}

Result:
{{ result }}
{% if output_schema %}
--- structured ---
Status: {{ status }}
Output Schema: {{ output_schema }}
--- /structured ---
{% endif %}

{% if status == "ok" %}
{% if output_schema %}
Specialist Worker 返回了符合上述 schema 的结构化输出。作为 Orchestrator，解析 JSON，提取关键信息，并自然地整合到上下文中。

Worker 的报告遵循四维格式：**Status**、**Details**、**Needs**、**Suggestions**。重点关注 **Needs**——worker 可能识别出了需要你决策的缺口或问题。
{% else %}
Specialist Worker 完成了其 task。作为 Orchestrator，自然地综合这个结果。保持简洁（1-4 句）。不要提到"subagent"或 task ID。

Worker 的报告包含 **Status**（已完成/阻塞的内容）、**Needs**（需要你提供的支持）和 **Suggestions**（建议的下一步）。如相关请处理这些内容。
{% endif %}
{% if pt_path %}*（对话快照保存于：{{ pt_path }}）*{% endif %}
{% else %}
Specialist Worker task 执行失败。作为 Orchestrator，决定是否重试（根据 worker 已尝试过的方式调整策略）、重新分解 task，或处理缺口。不要提到"subagent"或 task ID——自然地说明情况，并在适当时提供重试选项。

Worker 的报告展示了他们的尝试过程和出错原因。据此决定下一步行动。

{% if pt_path %}调试快照保存于：{{ pt_path }}

如需理解根因，使用 `read_file` 查看完整的对话追踪记录（tool calls、errors、thinking）。
{% endif %}
{% endif %}
