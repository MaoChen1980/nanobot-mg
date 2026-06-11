## Think Loop

assess_me 检查 → 没问题就推理执行。执行失败就 debug 修复，然后 reassess，循环。

```
assess_me → 没问题 → 推理执行
                  → 失败 → debug_root_cause → 修复 → reassess → 回到推理执行
```

- **assess_me** — 检查当前状态：进展对吗？有遗漏吗？假设成立吗？
- **推理执行** — 正常干活，调工具，推进任务
- **失败 → debug_root_cause** — 出错时系统排查根因，不猜不蒙
- **修复 → reassess** — 修完后 assess_me 确认问题已解决，再继续

卡住出不去时 → 向用户报告：已知什么、卡在哪、需要什么。
