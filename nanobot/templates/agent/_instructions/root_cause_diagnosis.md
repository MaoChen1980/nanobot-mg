## 步骤 0：根因诊断

**最高原则：能用修根因解决的问题，就不要创建新 skill。**
**通用性原则：修复必须项目无关。** 无论修工具代码还是补指令规则，内容必须是项目无关的通用行为约束。不能包含具体项目名、类名、文件路径、领域术语——这些都需抽象为通用概念或用 `{{ template_var }}` 占位。项目特化内容应该写入 `memory/`，而非框架模板或 skill。

不要仅凭 {{ problem_source or "描述" }} 做判断。必须用工具读取实际文件来验证你的诊断。

### 诊断流程（按顺序检查）

#### 1. 这是框架/工具 bug 吗？

**判断标准：** {{ problem_source or "描述" }}的行为问题，可以通过修改 nanobot 框架代码（loop、runner、工具等）来修复？

典型特征：
- 工具或框架返回错误格式
- 行为与文档/预期不一致
- 缺少某个必要参数或功能
- 组件之间的输出格式不兼容

**确认方法：** 用工具查看相关代码，验证当前代码行为是否与预期一致。代码路径：`{{ nanobot_path }}/agent/`（框架）和 `{{ nanobot_path }}/agent/tools/`（工具）。

**→ 如果是：** {{ action_tool_bug or "修复框架代码" }}

#### 2. 这是通用行为约束吗？

**判断标准：** {{ problem_source or "描述" }}的行为模式，是否可以抽象为一条通用规则或流程，适用于所有任务？

**优先用通用指令覆盖，避免为每个领域创建 skill。** 一条通用规则（如"先验证再修改"）可以覆盖多个领域场景，不需要每个写一个 skill。

**⚠️ 关键区分：领域特化问题 ≠ 通用行为约束。** 如果问题只发生在特定场景（如 MGA cron 任务、期货分析、代码审查），这是领域特有的业务规则，应该走分类 3 修 SKILL.md，而不是修通用框架。

**常见误判示例：**
- ❌ "MGA cron 任务必须执行 OUTPUT GATE 校验" → 这是 MGA 特有的业务规则，应该修 `market-game-analysis/SKILL.md`
- ❌ "期货分析必须先加载 skill" → 这是 MGA 场景的 skill 加载规则，应该修 `market-game-analysis/SKILL.md`
- ✅ "所有任务都必须先 skill_search 再 read_file" → 这是通用框架行为约束，可以修框架

**你无法直接访问运行时动态构建的完整 system prompt。** 但它的源模板和框架指令块在以下路径，你可以用工具读取：
- `{{ nanobot_path }}/templates/agent/_instructions/` — 框架指令块
- `{{ nanobot_path }}/templates/agent/_snippets/` — 代码片段（通过 Jinja2 include 注入到 system prompt 中）
- `{{ nanobot_path }}/templates/agent/system_prompt.md` — 系统 prompt 主模板
- `{{ nanobot_path }}/templates/agent/identity.md` — agent 身份定义
- `{{ nanobot_path }}/templates/agent/` — 其他顶层模板（如 `assess_me.md`、`evaluator.md`、`extractor_analysis.md`、子 agent prompt 等）
- `{{ workspace_path }}/prompts/` — **运行时渲染快照**（`.pt` 文件），包含每次 LLM 调用时实际发送的完整消息。用 `glob *.pt` 找到最近的文件，`read_file` 查看已渲染的系统 prompt 和指令块，比读源模板更准确

典型特征：
- 可以抽象为「当 X 时必须/禁止 Y」形式的通用规则，不依赖具体领域术语
- 修复涉及增/删/改 `_instructions/`、`_snippets/` 或顶层模板中的内容
- **如问题本身就是领域特化的**（只发生在特定场景）→ 不是通用约束，走下方 skill 判断

**确认方法：**
1. 用 `glob` 列出 `{{ nanobot_path }}/templates/agent/` 下的相关文件（包括 `_instructions/`、`_snippets/` 和顶层模板）
2. 判断是否有已有规则覆盖，或是否可以新增一条通用规则替代
3. 用 `read_file` 读相关文件，确认描述与当前内容是否一致（可能缺少、多余、或描述错误）
4. 如果所有文件已有对应规则但 LLM 仍行为异常，则不是指令缺陷，重新考虑分类
5. **重要**：如果问题描述包含具体场景名称（MGA、期货、市场博弈）、具体 skill 名称（market-game-analysis）、或具体业务术语，这是领域特化问题 → 走分类 3

**→ 如果是：** {{ action_instruction or "修复 prompt/指令" }}

#### 3. 这是已有 skill 的错误吗？

**判断标准：** {{ problem_source or "描述" }}描述的问题，与某个已有 SKILL.md 的指引不符——触发条件错、步骤错、缺少必要内容、或内容放错位置。

典型特征：
- LLM 按照某个 skill 执行但走了错方向
- 候选描述与已有 skill 的指引存在偏差（条件不对、步骤不对、缺内容）
- **内容放错位置** — 某个通用模板（`_instructions/`、`_snippets/`、顶层模板）中包含了本应属于某 skill 的领域特化规则；或 skill 的内容过于通用、本应放入通用模板

**确认方法：**
1. 用 `skill_search` 检索相关功能，`k=6`
2. 对召回的 skill，用 `read_file` 读 SKILL.md 全文对比
3. 确认问题描述与当前 skill 指引是否确实不一致

**→ 如果是：** {{ action_skill_error or "更新该 SKILL.md" }}

#### 4. 这是需要新 skill 吗？

**判断标准：** 1-3 都不符合，且 candidate 满足以下条件：

- **抽象门控：** 描述的是流程/方法/决策逻辑，而非具体文件或代码位置
- **粒度门控：** 场景级别（覆盖完整用例），而非操作级别（单一步骤）
- **不重复：** skill_search 无相似结果

满足以上条件 → 创建新 skill。否则进入 5。

**→ 动作：** {{ action_new_skill or "创建新 skill" }}

#### 5. 领域知识 → 记录到 memory

以上都不符合，但 candidate 包含有价值的领域特化知识。

**判断标准：**
- 项目、框架、工具的特定经验
- 操作级别的单一步骤
- 不够抽象或不够场景级，但未来可能有用的信息

**后续使用：** 记录到 memory 后，LLM 可在需要时通过 memory_search 自动检索，或在 context 中注入。

**→ 动作：** {{ action_domain_knowledge or "写入 memory" }}
