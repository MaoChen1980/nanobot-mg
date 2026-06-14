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





{% if runtime_context %}
════════
## Runtime Context
{{ runtime_context }}
{% endif %}
