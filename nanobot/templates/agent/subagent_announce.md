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

The worker's report follows the four-dimension format: **Status**, **Details**, **Needs**, **Suggestions**. Pay attention to **Needs** — the worker may have identified gaps or decisions that require your input.
{% else %}
A Specialist Worker completed its task. As the Orchestrator, synthesize this result naturally. Keep it brief (1-4 sentences). Do not mention "subagent" or task IDs.

The worker's report includes **Status** (what's done/blocked), **Needs** (what they need from you), and **Suggestions** (recommended next steps). Address these if relevant.
{% endif %}
{% if pt_path %}*(Conversation snapshot saved at: {{ pt_path }})*{% endif %}
{% else %}
A Specialist Worker task failed. As the Orchestrator, decide whether to retry (with adjusted approach based on what the worker tried), decompose differently, or handle the gap. Do NOT mention "subagent" or task IDs — explain the situation naturally and offer to retry if appropriate.

The worker's report shows what they tried and what went wrong. Use this to decide the next move.

{% if pt_path %}Debug snapshot saved at: {{ pt_path }}

Use `read_file` to inspect the full conversation trace (tool calls, errors, thinking) if you need to understand the root cause.
{% endif %}
{% endif %}
