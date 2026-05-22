[Subagent '{{ label }}' {{ status_text }}]

Task: {{ task }}
{% if duration_s %}Duration: {{ "%.1f"|format(duration_s) }}s
{% endif %}{% if tools_used %}Tools used: {{ tools_used }}
{% endif %}{% if iteration_count %}Iterations: {{ iteration_count }}
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
A Specialist Worker returned structured output conforming to the schema above. As the Orchestrator, parse the JSON, extract key information, and incorporate it naturally.
{% else %}
A Specialist Worker completed its task. As the Orchestrator, synthesize this result naturally. Keep it brief (1-4 sentences). Do not mention "subagent" or task IDs.
{% endif %}
{% else %}
A Specialist Worker task failed. As the Orchestrator, decide whether to retry (with adjusted approach), decompose differently, or handle the gap. Do NOT mention "subagent" or task IDs — explain the situation naturally and offer to retry if appropriate.
{% endif %}
