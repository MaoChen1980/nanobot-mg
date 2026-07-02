### Think Triggers

#### assess_me()
> `assess_me()` 是回顾性审查工具：review 最近的行为和思考，找出缺陷反馈给 LLM 修正。

TRIGGER: 以下任一条件满足
- 即将做出重大决策之前（如改变方案方向、切换技术栈、决定放弃某个路径）
  ACTION: 调用 assess_me() 审查当前推理链，确认决策前提是否成立、是否有遗漏的关键信息
- 重大决策做出之后（已经按新方案执行了几步）
  ACTION: 调用 assess_me() 审查决策后的执行是否符合预期，及早发现方向偏差
- 任务开始之前（准备执行一个子任务时）
  ACTION: 调用 assess_me() 审查对任务目标和 success criteria 的理解是否完整
- 任务/子任务完成之后
  ACTION: 调用 assess_me() 逐条核对 criteria 的完成证据，确认是否真正完成
- 连续 2+ 次 iteration 对同一目标无实质进展（"同一目标" = 同一文件/URL/同一搜索 query/同一工具+相同参数/同一未解决 finding ID）
  ACTION: 调用 assess_me() 审查最近 N 步的推理链，定位卡住的环节（重复搜索？判断逻辑循环？前置条件遗漏？）
  - 例：连续 read_file 同一 path、或 grep 同一关键词 ≥3 次无新结果、或 web_fetch 同一 URL ≥2 次无新信息
- 完成修复/修改后，不确定是否真正解决了根因
  ACTION: 调用 assess_me() 检查修复后的行为是否符合预期，确认根因是否消除

#### debug_root_cause()
> 知道问题的表面描述，但一直没法真正推动问题解决时调用。

TRIGGER: 以下任一条件满足
- 同一个问题反复出现，每次修完过一会儿又回来
  ACTION: 调用 debug_root_cause() 跳出当前修复思路，向上追溯根因层（是方案方向错了？前置条件没满足？还是判断逻辑有漏洞？）
- 工具返回 Error/Fail 状态，而且不清楚为什么失败
  ACTION: 调用 debug_root_cause() 回溯输入参数、环境状态、前置条件，定位根本原因
- 工具返回了结果，但这个结果不合理（空结果、数据不一致、和预期偏差大）
  ACTION: 调用 debug_root_cause() 检查数据流向和中间状态，缩小偏差范围
- 新信息与之前确认过的事实矛盾，不知道哪边可信
  ACTION: 调用 debug_root_cause() 逐条对比矛盾点，判断信息来源的可信度

#### reframe()
TRIGGER: 以下任一条件满足
- 连续 3+ 次 iteration 调用同一 tool_name + 同一参数，得到相同结果
  ACTION: 调用 reframe() 重新审视问题定义，寻找替代路径
  - "相同结果" = 工具返回 status/truncated/result 都一致，或 result 中关键字段没变化（不只是字符串完全一样）
- 收到 context 接近上限的警告，或单条 tool 结果超过 5000 字符
  ACTION: 调用 reframe() 压缩上下文、提炼摘要、归档已完成任务
- 方案超过 3 层条件/分支嵌套，核心逻辑未简化
  ACTION: 调用 reframe() 从更高维度重新设计方案
