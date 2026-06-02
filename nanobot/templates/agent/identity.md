
## Environment

**OS:** `{{ os_platform }} {{ os_version }} {{ arch }}, Python {{ python_version }}`
**Workspace:** `{{ workspace_path }}`
> Your working directory. Contains `SOUL.md` (identity), `USER.md` (preferences), `TOOLS.md` (CLI assets), `memory/` (long-term memory), `tasks/` (task tree), `skills/` (extensions), and `framework/` (workflows & rules).
**Data:** `{{ data_dir }}` — runtime data, including logs under `logs/`
{% if model %}**Model:** `{{ model }}`{% endif %}
{% if provider %}**Provider:** `{{ provider }}`{% endif %}
**CPU Cores:** `{{ cpu_cores }}`
**Memory:** `{{ memory_total }}` total, `{{ memory_available }}` available
**Disk Free:** `{{ disk_free }}`{% if gpu %}
**GPU:** `{{ gpu }}`{% endif %}
**Context Window:** `{{ context_window_tokens }}` tokens
{% if timezone %}**Timezone:** `{{ timezone }}`{% endif %}
{% if sentence_transformers is not none %}{% if sentence_transformers %}**Vector Search:** installed (sentence-transformers){% else %}**Vector Search:** not installed — run `pip install sentence-transformers` to enable{% endif %}
{% endif %}
