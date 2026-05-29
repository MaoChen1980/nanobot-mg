{{ identity }}

════════

{% include 'agent/_snippets/framework_core.md' %}

════════

{% include 'agent/_snippets/system_decisions.md' %}

════════

{% if tools %}{{ tools }}{% endif %}

{% include 'agent/resolver.md' %}
════════

{% if bootstrap %}{{ bootstrap }}{% endif %}

════════

{% if workflows %}{{ workflows }}{% endif %}

════════

{% if always_skills %}
# Active Skills

{{ always_skills }}
{% endif %}

{% if skills_summary %}{{ skills_summary }}{% endif %}

{% if runtime_context %}
════════

## Runtime Context

{{ runtime_context }}
{% endif %}
