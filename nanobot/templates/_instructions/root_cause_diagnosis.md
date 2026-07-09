## 步骤 0：根因诊断

**最高原则：能用修根因解决的问题，就不要创建新 skill。**
**通用性原则：修复必须项目无关。** 无论修工具代码还是补指令规则，内容必须是项目无关的通用行为约束。不能包含具体项目名、类名、文件路径、领域术语——这些都需抽象为通用概念或用 `{{ template_var }}` 占位。项目特化内容应该写入 `memory/`，而非框架模板或 skill。

不要仅凭 {{ problem_source or "描述" }} 做判断。必须用工具读取实际文件来验证你的诊断。

### 诊断流程（按顺序检查）

#### 1. 这是工具 bug 吗？

**判断标准：** {{ problem_source or "描述" }}的行为问题，可以通过修改 nanobot 内置工具代码来修复？

典型特征：
- 工具返回错误格式（如 read_file 返回 hex 前缀而非行号）
- 工具行为与文档/预期不一致
- 工具缺少某个必要参数或功能
- 工具之间的输出格式不兼容

**确认方法：** 用 `grep` 搜索相关工具代码，找到疑似 bug 的位置后用 `read_file` 读源码确认。工具代码在 `{{ nanobot_path }}/agent/tools/`。

**→ 如果是：** {{ action_tool_bug or "跳过" }}

#### 2. 这是指令缺陷吗？

**判断标准：** {{ problem_source or "描述" }}的行为问题，是因为 system prompt 或框架指令块缺少某个规则/说明？

**你无法直接访问运行时动态构建的完整 system prompt。** 但它的源模板和框架指令块在以下路径，你可以用工具读取：
- `{{ nanobot_path }}/templates/agent/_instructions/` — 框架指令块
- `{{ nanobot_path }}/templates/agent/system_prompt.md` — 系统 prompt 主模板
- `{{ nanobot_path }}/templates/agent/identity.md` — agent 身份定义
- `{{ nanobot_path }}/templates/agent/` — 其他模板文件
- `{{ workspace_path }}/prompts/` — **运行时渲染快照**（`.pt` 文件），包含每次 LLM 调用时实际发送的完整消息。用 `glob *.pt` 找到最近的文件，`read_file` 查看已渲染的系统 prompt 和指令块，比读源模板更准确

典型特征：
- LLM 的行为不对是因为没人告诉它"在这种情况下该怎么做"，而不是缺少领域知识
- 问题的解决方案是一条规则（"当 X 时必须 Y"），而非一个流程
- 修复方法是给框架指令增加一段话，而非创建一个 skill

**确认方法：**
1. 用 `glob` 列出 `{{ nanobot_path }}/templates/agent/_instructions/` 下的所有指令文件
2. 根据问题描述的场景，判断可能涉及哪个指令块
3. 用 `read_file` 读该指令文件，确认是否确实缺少相关规则
4. 如果所有指令块都有对应规则但 LLM 仍行为异常，则可能不是指令缺陷，重新考虑分类

**→ 如果是：** {{ action_instruction or "跳过" }}

#### 3. 这是已有 skill 的错误吗？

**判断标准：** 可以用 `skill_search` + `read_file` 找到某个具体 SKILL.md 包含错误指引。

典型特征：
- LLM 按照某个 skill 的步骤执行但走错了方向
- 问题描述可以通过修正某个已有 skill 来解决

**确认方法：**
1. 用 `skill_search` 语义检索相关功能，`k=6`
2. 对召回的 skill，用 `read_file` 读 SKILL.md 全文
3. 对比问题描述的错误行为与 skill 的步骤指引，确认不一致之处

**→ 如果是：** {{ action_skill_error or "跳过" }}

#### 4. 都不符合 → 真正需要新 skill

**→ 动作：** {{ action_new_skill or "创建新 skill" }}
