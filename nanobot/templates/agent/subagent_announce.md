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
{% if pt_path %}*（对话快照保存于：{{ pt_path }}）*{% endif %}
