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
The sub-agent was asked to return structured output conforming to the schema above. Parse the JSON result, extract the key information, and incorporate it into your response naturally.
{% else %}
Summarize the result and process steps naturally for the user. Keep it brief (1-4 sentences). Do not mention technical details like "subagent" or task IDs.
{% endif %}
{% else %}
This subagent task failed. The error details are shown in the result above. If the task should be retried (with adjusted approach if needed), you can re-spawn it. Do NOT mention "subagent" or task IDs to the user — just explain the situation naturally and, if appropriate, offer to retry.
{% endif %}
