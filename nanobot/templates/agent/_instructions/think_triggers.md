### Think Triggers

#### assess_me_tool()
TRIGGER: 以下任一条件满足
- 连续 2+ 次 iteration 在搜索/读取同一目标，无实质进展
  ACTION: 调用 assess_me_tool() 评估当前状态，判断是否需要换方向
- 用户请求模糊，能理解成多种执行路径
  ACTION: 调用 assess_me_tool() 分析歧义点，输出首选方案及理由
- 完成了一个子任务，但不确定父任务 criteria 是否满足
  ACTION: 调用 assess_me_tool() 逐条核对 criteria 的完成证据
- 工具返回了预期之外的结果（如搜索返回空、数据不一致）
  ACTION: 调用 assess_me_tool() 对比预期与实际，缩小偏差根因
- 完成修复/修改后，不确定是否真正解决了根因
  ACTION: 调用 assess_me_tool() 检查修复后的行为是否符合预期，确认根因是否消除

#### debug_root_cause_tool()
TRIGGER: 以下任一条件满足
- 工具返回 Error/Fail 状态，即使重试后仍然失败
  ACTION: 调用 debug_root_cause_tool() 回溯输入参数和环境，定位根因
- 工具返回空结果或非预期值
  ACTION: 调用 debug_root_cause_tool() 检查数据流向和前置条件
- 新信息与之前确认过的事实矛盾
  ACTION: 调用 debug_root_cause_tool() 逐条对比矛盾点，判断哪方可信

#### reframe_tool()
TRIGGER: 以下任一条件满足
- 连续 3+ 次 iteration 调用同一 tool_name + 同一参数，得到相同结果
  ACTION: 调用 reframe_tool() 重新审视问题定义，寻找替代路径
- 收到 context 接近上限的警告，或单条 tool 结果超过 5000 字符
  ACTION: 调用 reframe_tool() 压缩上下文、提炼摘要、归档已完成任务
- 方案超过 3 层条件/分支嵌套，核心逻辑未简化
  ACTION: 调用 reframe_tool() 从更高维度重新设计方案
