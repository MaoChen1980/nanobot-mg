{# Subagent 结果公告模板 — orchestrator 接收 subagent 完成/退回结果时的结构化展示格式，含自检标记和时间统计 #}
[Subagent '{{ label }}' {{ status_text }}]
{% if status == "needs_review" %}⚠️ 自检发现问题，需要 Orchestrator 审查后再使用结果
{% endif %}

Task: {{ task }}
{% if duration_s %}耗时：{{ "%.1f"|format(duration_s) }}s
{% endif %}{% if tools_used %}使用工具：{{ tools_used }}
{% endif %}{% if iteration_count %}迭代次数：{{ iteration_count }}
{% endif %}

Result:
{{ result }}
{% if assessment %}--- self-assessment ---
{{ assessment | truncate(600) }}

--- /self-assessment ---
{% endif %}{% if output_schema %}
--- structured ---
Status: {{ status }}
Output Schema: {{ output_schema }}
--- /structured ---
{% endif %}
{% if pt_path %}*（对话快照保存于：{{ pt_path }}）*{% endif %}
