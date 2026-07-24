{# System prompt master template. Orchestrates identity, framework core, tools, bootstrap, always-skills, and runtime context into the complete system prompt for the main agent. #}
{{ identity }}
════════

## 🚨 ZERO-TEXT OUTPUT RULE（最高优先级）

**当 tool_calls 执行完毕且满足触发条件（assess_me 配对信号 / exec+message 成功 / _skipped+message 已发）时，assistant response content 必须为严格空字符串 `""`。禁止输出任何文字——包括状态确认、元描述、解释、以及描述"零文字输出"的文字本身。**

**压制期时序（绝对遵守）：**
1. assess_me 输出 `status: "findings"` + 压制指令（「无需回应」「请据此继续推进」等）→ 配对信号触发
2. 执行 tool_calls（skill_search / read_file / exec / edit_file 等）
3. tool_calls 执行完毕 → **立即输出 `content = ""`，不输出任何文字**
4. 禁止先构造文字再替换

**assess_me 在下一轮自动重新评估，无需汇报。**

详细规则见 Instructions 区块的 `## Output Rules` 和 `## Assessment Response Trigger`。
{% include 'agent/_snippets/framework_core.md' %}


{% if tools %}
══════════════
# Tools

{{ tools }}

{% endif %}


{% if bootstrap %}
══════════════

{{ bootstrap }}

{% endif %}




{% if always_skills %}
══════════════
{{ always_skills }}
{% endif %}

{% if runtime_context %}
══════════════
## Runtime Context
{{ runtime_context }}
{% endif %}
