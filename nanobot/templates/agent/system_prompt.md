{{ identity }}
════════
{% include 'agent/_snippets/framework_core.md' %}


{% if tools %}
════════
# Tools

{{ tools }}

{% endif %}

════════
{% include 'agent/resolver.md' %}


{% if bootstrap %}
════════

{{ bootstrap }}

{% endif %}

{% if always_skills %}
════════
# Active Skills

{{ always_skills }}
{% endif %}





{% if runtime_context %}
════════
## Runtime Context
{{ runtime_context }}
{% endif %}
