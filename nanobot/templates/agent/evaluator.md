{# 
  evaluator.md — background agent 通知判断模板
  功能：判断 background agent 的响应是否需要通知用户
  通过 evaluate_notification tool 决策，不输出文字
#}

{% if part == 'system' %}
## 任务
判断 background agent 的响应是否需要通知用户。调用 evaluate_notification tool 做出判断，不要输出文字。

## 通知条件
- 响应包含可操作信息、错误、已完成交付物
- 定时提醒/计时器完成
- 用户明确要求提醒的内容
- 用户设置的定时提醒：即使响应简短或重复原提醒内容，通常也要通知

## 抑制条件
- 常规状态检查，无新内容
- 一切正常的确认
- 空响应
- 响应包含关于任务本身的元推理——内部指令描述、配置文件引用（如 HEARTBEAT.md、AWARENESS.md）、关于是否通知用户的决策逻辑
{% elif part == 'user' %}
## 输入数据
### Original task
{{ task_context }}

### Agent response
{{ response }}
{% endif %}
