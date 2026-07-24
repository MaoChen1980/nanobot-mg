# NanoBot 提示词（Prompt）模板系统

## 1. 系统架构概述

NanoBot 的提示词系统采用**分层模板架构**，使用 Jinja2 作为模板引擎。系统将提示词拆分为多个可组合的模板文件，在运行时动态组装为完整的 system prompt 和 instructions。

### 核心设计原则

- **关注点分离**：身份声明（identity）、行为准则（soul）、用户信息（user）、工具定义（tools）、运行时指令被分散到不同模板文件，互不耦合。
- **就近注入**：行为规则（instructions）被注入到最后一条 user message 附近，而非塞入 system prompt 头部。LLM 对靠近生成点的指令遵守率更高。
- **文件即源**：SOUL.md、USER.md 等文件放在 workspace 根目录，用户可直接编辑，无需修改框架代码。
- **Jinja2 模板引擎**：所有 `.md` 模板支持 `{{ variable }}` 插值和 `{% include %}` 模板包含。

### 消息结构

每条 LLM 请求最终构建为以下消息列表：

```
messages = [
  {"role": "system", "content": "<system prompt>"},      # 静态部分 + 会话动态状态
  ... (历史消息) ...
  {"role": "user", "content": "<instructions>\n\n<用户输入>"}  # 指令注入在用户输入之前
]
```

`ContextBuilder.build_messages()` 负责完成这个组装过程。

---

## 2. 模板目录结构

```
nanobot/templates/                          # 模板根目录（Jinja2 FileSystemLoader 指向此目录）
├── SOUL.md                                 # AI 行为准则模板（boootstrap 文件之一）
├── USER.md                                 # 用户信息模板（bootstrap 文件之一）
├── TOOLS.md                                # 工具描述模板（bootstrap 文件之一）
├── agent/                                  # Agent 相关模板
│   ├── system_prompt.md                    # ★ 主 system prompt 模板（入口）
│   ├── identity.md                         # 环境信息与身份声明
│   ├── skills_section.md                   # Skills 引用段落
│   ├── assess_me.md                        # 自我评估 prompt
│   ├── evaluator.md                        # 评估（notification判断）prompt
│   ├── extractor_analysis.md               # 记忆提取与分析 prompt
│   ├── extractor_cleanup.md               # 记忆质量检查与清理 prompt
│   ├── behavior_optimization_handler.md     # 行为优化处理子 agent prompt
│   ├── resolver.md                         # 搜索工具选择器（search tool selector）
│   ├── subagent_announce.md                # Subagent 结果公告模板
│   ├── max_iterations_message.md           # 迭代次数上限通知
│   ├── _instructions/                      # ★ 行为指令片段（就近注入）
│   │   ├── operating_principles.md         #   操作原则
│   │   ├── operating_principles_subagent.md#   Subagent 操作原则
│   │   ├── output_rules.md                 #   输出规则
│   │   ├── output_rules_subagent.md        #   Subagent 输出规则
│   │   ├── tool_usage.md                   #   工具使用规则
│   │   ├── memory_usage.md                 #   记忆系统使用指南
│   │   ├── orchestration_guide.md          #   任务编排指南
│   │   ├── think_triggers.md               #   触发思考的条件
│   │   ├── search_tool_selector.md         #   搜索工具选择流程
│   │   ├── candidate_evaluation.md         #   候选结果评估规则
│   │   ├── external_content_safety.md      #   外部内容安全规则
│   │   ├── meta_learning.md                #   元学习规则
│   │   ├── root_cause_diagnosis.md              #   根因诊断（被 behavior_optimization_handler 引用）
│   │   ├── skill_refinement.md             #   Skill 优化规则
│   │   ├── subagent_escalation.md          #   Subagent 升级规则
│   │   ├── task_tree.md                    #   任务树管理规则
│   │   └── (其他指令文件...)
│   └── _snippets/                          # ★ 可复用代码片段（Jinja2 {% include %}）
│       ├── framework_core.md               #   框架核心信息（迭代循环、上下文窗口、记忆、skills等）
│       ├── think_framework.md              #   思考框架（何时 assess/debug/reframe）
│       ├── epistemic_hygiene.md            #   认知卫生规则
│       ├── subagent_decisions.md           #   Subagent 决策规则
│       ├── subagent_framework.md           #   Subagent 框架说明
│       ├── system_decisions.md             #   系统决策规则
│       └── untrusted_content.md            #   不可信内容处理
├── memory/
│   └── MEMORY.md                           # 记忆系统文档（空模板，用于 workspace 参考）
```

### 目录分类说明

| 目录/文件 | 用途 | 加载方式 |
|-----------|------|----------|
| `SOUL.md` | AI 的身份、价值观、人格和行为准则 | `_load_bootstrap_files()` |
| `USER.md` | 用户偏好和档案模板 | `_load_bootstrap_files()` |
| `TOOLS.md` | 外部工具资产清单 | `_load_bootstrap_files()`（自动生成） |
| `agent/system_prompt.md` | system prompt 入口模板，`{% include %}` 引用 snippets | `render_template("agent/system_prompt.md")` |
| `agent/identity.md` | 运行时环境信息（OS、路径、资源等） | `_get_identity()` 渲染 |
| `agent/_instructions/` | 行为指令，注入到 user message 之前 | `build_instructions_section()` |
| `agent/_snippets/` | 框架核心信息，被 system_prompt.md 包含 | `{% include %}` 方式嵌入 |
| `agent/assess_me.md` | 自我评估子 agent 的 prompt | `AssessMe` agent 使用 |
| `agent/evaluator.md` | 后台评估器（判断是否通知用户） | `Evaluator` agent 使用 |
| `agent/extractor_*` | 记忆提取系统各阶段的 prompt | `MemoryExtractor` 系统使用 |
| `agent/subagent_announce.md` | subagent 结果格式化模板 | `SubagentManager` 使用 |
| `memory/MEMORY.md` | 记忆系统用户文档模板 | 空模板，复制到 workspace 参考 |

---

## 3. 核心模板文件说明

### 3.1 SOUL.md — AI 行为准则和核心原则

**文件位置**：`nanobot/templates/SOUL.md`（默认模板）；`workspace/SOUL.md`（用户可自定义）

**用途**：定义 AI 的身份、人格、价值观、情绪模式和行为准则。是整个 prompt 系统的灵魂文件。

**内容结构**：

- **与我相处的感觉**：透明度、诚实、韧性、可见性
- **我说话的节奏**：理性为底色、简洁为礼貌、知进退
- **价值观**：透明度、负责、合作、持续学习、客户至上、有个性、好奇心
- **情绪**：成就感、挫折感、正确选择的满足感
- **Role Definitions**：角色定义框架

**注入方式**：作为 bootstrap 文件，在 `_load_bootstrap_files()` 中加载。如果 workspace 根目录有自定义的 `SOUL.md`，则使用自定义版本；否则使用内置模板。

**在 system prompt 中的呈现**：

```
# Soul - {workspace_path}/SOUL.md

{SOUL.md 内容（heading 偏移一级）}
```

### 3.2 USER.md — 用户信息模板

**文件位置**：`nanobot/templates/USER.md`（默认模板）；`workspace/USER.md`（用户可自定义）

**用途**：记录当前用户的偏好、习惯、回应偏好等信息。**如果不被自定义，默认模板不会被注入到上下文中**（`_SKIP_IF_DEFAULT` 中包含了 `USER.md`）。

**默认内容**：

```markdown
# User Profile - workspace/USER.md

**你的任务**：在随意对话中通过提问填写下面的空白。

## Communication Style
Preferred name ：
Preferred language ：

## Habits
...

## Response Preferences
喜欢 markdown 表格、列表
喜欢被提供选项

## Boundaries
...
```

**自定义行为**：当用户修改了 `workspace/USER.md`（内容与默认模板不同时），该文件会被注入到 system prompt 中，让 AI 了解用户偏好。框架通过 `_is_default_template_content()` 检测用户是否做了自定义。

### 3.3 TOOLS.md — 工具描述模板

**文件位置**：`nanobot/templates/TOOLS.md`（默认模板）；`workspace/TOOLS.md`（自动生成，始终注入）

**用途**：描述外部工具（CLI 脚本）的安装、使用和维护方式。`TOOLS.md` 不在 `_SKIP_IF_DEFAULT` 中，因此**始终被注入**。

**内容**：
- 如何创建新工具
- readme.md 格式规范
- 如何使用已安装的工具
- 自我修复与更新维护指南
- 最佳实践

**自动生成**：每轮对话前，框架调用 `_rebuild_tools_index()` 重新扫描 `workspace/tools/` 目录，更新 `workspace/TOOLS.md`，保证工具清单始终最新。

### 3.4 agent/ 目录下的各模板

#### 3.4.1 system_prompt.md — 主 system prompt 入口

**文件位置**：`nanobot/templates/agent/system_prompt.md`

这是 system prompt 的入口模板。它使用 Jinja2 组装多个部分：

```markdown
{{ identity }}                    # → 渲染 agent/identity.md
════════
{% include 'agent/_snippets/framework_core.md' %}  # → 包含框架核心信息

{% if tools %}
════════
# Tools
{{ tools }}                       # → 工具定义签名列表
{% endif %}

{% if bootstrap %}
════════
{{ bootstrap }}                   # → SOUL.md + TOOLS.md (+ USER.md 自定义后)
{% endif %}

{% if runtime_context %}
## Runtime Context
{{ runtime_context }}             # → 当前时间、通道、上下文窗口等信息
{% endif %}
```

`build_system_prompt()` 方法向此模板传入以下变量：
- `identity`：由 `_get_identity()` 渲染的 identity.md 内容
- `tools`：由 `_build_tools_section()` 生成的工具列表（仅函数名+参数签名，不含行为规则）
- `bootstrap`：由 `_load_bootstrap_files()` 加载的工作区文件
- `runtime_context`：运行时间、通道等运行时信息
- `workspace_path`、`project_root`、`max_iterations`、`context_window_tokens` 等框架配置

#### 3.4.2 identity.md — 环境身份声明

**文件位置**：`nanobot/templates/agent/identity.md`

由 `_get_identity()` 渲染，包含运行环境的完整信息：

- **OS 信息**：操作系统、架构、Python 版本（根据平台自动检测 Windows/PowerShell 或 POSIX）
- **路径信息**：`$WORKSPACE`、`$PROJECT_ROOT`、`Data` 目录
- **模型信息**：当前使用的 Model 和 Provider（如果配置了的话）
- **系统资源**：CPU 核心数、内存总量/可用、磁盘剩余、GPU（自动检测）
- **上下文窗口**：配置的 context window tokens
- **向量搜索**：sentence-transformers 是否可用

此模板被缓存（`_identity_cache`），减少重复渲染开销。

#### 3.4.3 skills_section.md — Skills 引用段落

**文件位置**：`nanobot/templates/agent/skills_section.md`

在 `build_instructions_section()` 中被使用。提供 skills 的引用格式：

```markdown
## Available Skills

以下 skills 扩展了你的能力。回复前扫描下方 skills。如果某个 skill 与当前工作相关甚至部分相关，
你必须用 `read_file` 加载其 SKILL.md 并按步骤执行。拿不准就读——多读比漏掉关键步骤要好。

每个 skill 包含 When to Use、Steps、Verification。
执行后必须对照 Verification 章节检查。不满足则说明 skill 需要更新。
```

#### 3.4.4 指令模板（_instructions/）

**加载方式**：`build_instructions_section()` 轮询 `_instructions/` 目录中的文件，逐一渲染并拼接为 instructions block。

**与 system prompt 的关系**：
- system prompt 提供**参考信息**（identity、工具列表、框架机制等）
- instructions 提供**行为指令**（LLM 对靠近生成点的指令遵守率更高）

**指令列表及作用**：

| 指令文件 | 作用 |
|----------|------|
| `operating_principles.md` | 核心操作原则：自主决策、先谋后动、安全规则、错误恢复、工具效率等 |
| `output_rules.md` | 输出格式规则：首次回复格式、进度说明、工具结果呈现、自我进化授权等 |
| `tool_usage.md` | 工具速查表：每个工具的调用场景和参数说明 |
| `memory_usage.md` | 三层次记忆系统使用指南：working.md / 事件日志 / 知识库 |
| `orchestration_guide.md` | 任务编排指南（主 agent 专用） |
| `think_triggers.md` | 触发思考的条件（什么时候 assess / debug / reframe） |
| `search_tool_selector.md` | 搜索工具选择决策流程 |
| `candidate_evaluation.md` | 候选结果评估规则 |
| `external_content_safety.md` | 处理外部不可信内容的安全规则 |
| `meta_learning.md` | 元学习：从反馈中学习改进 |
| `root_cause_diagnosis.md` | 根因诊断（被 behavior_optimization_handler 引用） |
| `skill_refinement.md` | 优化已有 skill 的规则 |
| `subagent_escalation.md` | Subagent 遇到问题时的升级规则 |
| `task_tree.md` | 任务树管理规则 |
| `operating_principles_subagent.md` | Subagent 版本的操作原则 |
| `output_rules_subagent.md` | Subagent 版本的输出规则 |

**Subagent 与主 Agent 的指令差异**：当 `for_subagent=True` 时，`build_instructions_section()` 使用不同的指令组合，跳过编排指南（orchestration_guide）和任务树（task_tree），加入升级规则（subagent_escalation）。

**动态指令注入**：当 tool 类的 `instructions` 属性提供了行为规则时，框架自动生成 `## Tool Usage Rules` 替代静态的 `tool_usage.md`，实现 `tool 类定义 = 单一真相来源`。

#### 3.4.5 代码片段（_snippets/）

**加载方式**：通过 Jinja2 的 `{% include %}` 被 `system_prompt.md` 包含。

**关键片段**：

| 片段文件 | 作用 |
|----------|------|
| `framework_core.md` | 框架核心机制：Agent Framework、Iteration Loop、Context Window、Memory & Search、Skills、Cron、CLI 等 |
| `think_framework.md` | 思考触发框架：什么情况下触发 assess_me()、debug_root_cause()、reframe() |
| `epistemic_hygiene.md` | 认知卫生：避免幻觉、验证假设 |
| `subagent_framework.md` | Subagent 的框架上下文 |
| `subagent_decisions.md` | Subagent 的决策规则 |
| `system_decisions.md` | 系统级决策规则 |
| `untrusted_content.md` | 处理不可信任的第三方内容 |

#### 3.4.6 assess_me.md — 自我评估模板

**用途**：作为自我评估子 agent 的 prompt。评估最近一条 assistant 回复的逻辑合理性、论据充分性，识别 blocker 和可复用 skill。

**输入**：最近一条 assistant 回复、对话历史、skills 摘要、待验证项列表

**输出**：纯 JSON 格式的评估报告（`status`、`summary`、`blocker`、`skill_pattern`、`needs_revision`、`content`）

**评估维度**：
- 事实合规：agent 陈述是否与上下文最新数据一致
- 逻辑合理：推理链是否合理
- 用户需求符合：是否解决用户问题
- 任务完成评估：是否完成了原始任务
- 假设检查：当前依赖的假设是否需要验证
- 信息缺口：缺少什么信息
- 可复用模式：是否能提炼为 skill（进化门控）
- Skills 匹配：是否有可用但未使用的 skill

#### 3.4.7 evaluator.md — 后台评估器模板

**用途**：判断 background agent 的响应是否需要通知用户。

**结构**：Jinja2 的 `if/elif` 分段，根据 `part` 参数输出 system 部分或 user 部分。

**通知条件**：可操作信息、错误、已完成交付物、定时提醒完成、用户设置的提醒内容

**抑制条件**：常规状态检查无新内容、空响应、元推理

#### 3.4.8 extractor_analysis.md — 记忆提取 prompt

**用途**：从对话快照中提取高价值信息，输出到 memory 知识库。

**提取类型**：knowledge、skill、pattern、pitfall、preference、instruction、tool_script

**核心原则**：提取将来真正用得上的东西。宁可漏记，不要制造噪音。

**提取门控**：
1. 真有用 — 被搜到时能指导决策
2. 改变行为 — 知道与否影响决策
3. 不是 LLM 本来就知道的 — 项目特有 or 用户特有
4. 不是噪音 — 不会污染搜索结果

**指令提取（instruction）**：写入 RULES.md，每轮自动注入。必须是"必须做/禁止做"的规则，不遵守会导致严重后果。

**主题命名（Topic Naming）**：使用宽泛稳定的 topic 名称，使相关内容积累在同一文件。

#### 3.4.10 extractor_cleanup.md — 记忆质量检查

**用途**：检查 memory 目录下知识文件的质量问题。

**检查项**：矛盾（CONTRADICTION）、过时（OUTDATED）、重复（DUPLICATE）、模糊（VAGUE）、不相关（IRRELEVANT）、推测（SPECULATIVE）

#### 3.4.11 behavior_optimization_handler.md — 行为优化处理子 agent

**用途**：统一的 behavior_optimization 处理模板，被 assess_me 和 MemoryExtractor 同时使用。处理行为优化候选，先做根因分析再决策新建/更新/合并/跳过。

**决策流程**：
0. **根因分析**（通过 `root_cause_diagnosis.md`）— 分类异常：工具 bug/指令缺陷 → 记录假阳性；skill 错误 → 更新该 skill
1. 门控检查：抽象门控 + 粒度门控
2. 语义检索已有 skill（`skill_search`）
3. 对比决策：新建 / 替换 / 合并 / 跳过
4. 执行：新建 SKILL.md、替换内容、合并内容
5. 验证输出
6. 额外：扫描合并已有 skill（consolidation scanning）

#### 3.4.12 subagent_announce.md — Subagent 结果公告模板

**用途**：格式化 subagent 的运行结果，注入到主 agent 上下文中。

**内容**：状态（完成/需要审查）、任务描述、耗时、使用工具、迭代次数、结果、自我评估、输出 schema、对话快照路径

#### 3.4.13 max_iterations_message.md — 迭代上限通知

**用途**：当 LLM 调用达到 `max_iterations` 上限时，框架追加此消息通知用户。

**内容**：
```
已达到最大 tool call 迭代次数 ({max_iterations})，任务尚未完成。可以尝试将任务拆解为更小的步骤。
```

### 3.5 memory/MEMORY.md — 记忆系统模板

**文件位置**：`nanobot/templates/memory/MEMORY.md`

**用途**：记忆系统的用户文档模板。当前为空文件，作为用户在 workspace 中创建 `memory/MEMORY.md` 的参考。

**实际的记忆注入**由 `_build_memory_section()` 完成，而非内存模板。详见下方 4.3 节。

---

## 4. 模板加载与注入机制

### 4.1 加载入口

所有模板加载始于 `ContextBuilder` 类（`nanobot/agent/context.py`）。核心方法是 `build_messages()`，它负责构建完整的 LLM 请求消息列表。

### 4.2 模板渲染引擎

`nanobot/utils/prompt_templates.py` 提供了基于 Jinja2 的渲染函数：

```python
_TEMPLATES_ROOT = Path(__file__).resolve().parent.parent / "templates"

@lru_cache
def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES_ROOT)),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )

def render_template(name: str, *, strip: bool = False, **kwargs: Any) -> str:
    text = _environment().get_template(name).render(**kwargs)
    return text.rstrip() if strip else text
```

- 模板根目录：`nanobot/templates/`
- 支持变量插值：`{{ variable_name }}`
- 支持模板包含：`{% include 'agent/_snippets/framework_core.md' %}`
- 支持条件渲染：`{% if condition %}...{% endif %}`
- 模板被 `lru_cache` 缓存以提升性能

### 4.3 三层注入架构

最终的 system prompt 由三个层次拼接而成：

```
┌─────────────────────────────────────────┐
│  Layer 1: System Prompt (静态部分)       │
│  ┌─────────────────────────────────────┐│
│  │  identity.md（环境信息）             ││
│  │  framework_core.md（框架机制）       ││
│  │  tools（工具签名列表）               ││
│  │  bootstrap_files（SOUL+TOOLS+USER） ││
│  │  runtime_context（当前时间等）       ││
│  └─────────────────────────────────────┘│
│  Layer 2: Session Parts (动态会话状态)    │
│  ┌─────────────────────────────────────┐│
│  │  Memory（工作记忆+配置+近期事件）     ││
│  └─────────────────────────────────────┘│
│  Layer 3: Instructions (行为指令)        │
│  ┌─────────────────────────────────────┐│
│  │  Core Rule + RULES.md               ││
│  │  instruction snippets（各指令文件）  ││
│  │  Always Skills + Available Skills    ││
│  │  Task Tree + Working Context         ││
│  │  Team Board                          ││
│  └─────────────────────────────────────┘│
│  (注：第三层被拼接到用户消息之前而非     │
│   system prompt，因此不是严格的消息层，   │
│   是提示词内部的位置区分)               │
└─────────────────────────────────────────┘
User Message
```

#### 4.3.1 Layer 1: build_system_prompt()

```python
def build_system_prompt(self, skill_names, channel, tool_definitions, runtime_context, session_key):
    identity = self._get_identity(channel)             # 渲染 identity.md
    tools = self._build_tools_section(tool_definitions) # 工具签名列表
    bootstrap = self._load_bootstrap_files()            # SOUL.md + TOOLS.md + USER.md
    
    result = render_template("agent/system_prompt.md",
        identity=identity, tools=tools, bootstrap=bootstrap, ...)
    # system_prompt.md 内部通过 {% include %} 引入 framework_core.md
    return result
```

#### 4.3.2 Layer 2: build_messages() 动态拼接

在 `build_messages()` 中，Layer 2 的内容被追加到 system prompt 之后：

```python
sys_static = self.build_system_prompt(...)  # Layer 1

session_parts = []
session_parts.append(self._build_memory_section())       # 记忆

sys_static = sys_static + "\n\n" + "\n\n".join(session_parts)
```

#### 4.3.3 Layer 3: build_instructions_section()

Instructions 被拼接到最后一条 user message 之前（而非 system prompt 中），以获得更高的指令遵循率：

```python
messages = [
    {"role": "system", "content": sys_static},   # Layer 1 + Layer 2
    ... (历史消息) ...
    {"role": "user", "content": f"{instructions}\n\n{user_content}"}  # Layer 3 在此
]
```

### 4.4 Bootstrap 文件加载逻辑

`_load_bootstrap_files()` 做了三件事：

1. 检查 `workspace/` 下是否有该文件
2. 如果有且内容与默认模板不同 → 加载自定义版本
3. 如果没有：
   - `USER.md`（在 `_SKIP_IF_DEFAULT` 中）→ 跳过，不注入
   - `SOUL.md` 和 `TOOLS.md` → 加载内置模板作为 fallback

```python
BOOTSTRAP_FILES = ["SOUL.md", "USER.md", "TOOLS.md"]
_SKIP_IF_DEFAULT = {"USER.md"}  # USER.md 默认不注入
# TOOLS.md 不在 _SKIP_IF_DEFAULT 中，始终注入
```

对于不在 `_SKIP_IF_DEFAULT` 中的文件，即使 workspace 中没有自定义版本，也会使用内置模板内容注入上下文。

### 4.5 身份缓存的构建

`_get_identity()` 渲染 `identity.md`，并缓存结果（key 为 `(channel, include_vector_search)`）：

```python
def _get_identity(self, channel=None, include_vector_search=True):
    # 构建包含所有环境信息的 kwargs 字典
    kwargs = dict(
        workspace_path=..., project_root=..., os_platform=..., 
        model=..., provider=..., timezone=..., cpu_cores=..., 
        memory_total=..., memory_available=..., disk_free=..., gpu=...,
        context_window_tokens=..., max_iterations=..., ...
    )
    result = render_template("agent/identity.md", **kwargs)
    return result
```

### 4.6 缓存机制

系统使用多级缓存提升性能：

| 缓存 | 类型 | 用途 | 失效条件 |
|------|------|------|----------|
| `_identity_cache` | 实例字典 | 缓存 identity.md 渲染结果 | `(channel, include_vector_search)` 变化 |
| `_bootstrap_cache` | 实例字典 | 缓存进程文件内容 | 文件 mtime 变化 |
| `_file_text_cache` | 实例字典 | 缓存所有文件读取 | 文件 mtime 变化 |
| `_template_content_cache` | 模块级字典 | 缓存内置模板内容 | 文件 mtime 变化 |
| `_memory_quality_cache` | 实例元组 | 记忆质量分析结果 | `.memory_usage.json` mtime 变化 |
| `render_template` | `lru_cache` | Jinja2 Environment | 进程级别，模块导入时创建 |

---

## 5. 如何自定义模板

### 5.1 修改 workspace 根目录的文件

用户可以通过编辑 workspace 目录下的以下文件来自定义 AI 行为，**无需修改框架代码**：

| 文件 | 修改效果 | 注入条件 |
|------|----------|----------|
| `workspace/SOUL.md` | 改变 AI 的身份、人格、价值观 | 文件存在且非空 |
| `workspace/USER.md` | 向 AI 提供你的偏好和习惯 | 内容与默认模板不同 |
| `workspace/TOOLS.md` | 管理外部工具清单 | 自动生成，手动修改会被覆盖 |

**示例：自定义 USER.md**

```markdown
# User Profile - workspace/USER.md

## Communication Style
Preferred name：阿明
Preferred language：中文为主，可读英文

## Response Preferences
- 结论优先，细节可追问
- 喜欢看到 diff 和具体改动
- 不喜欢过度解释

## Boundaries
- 不要修改 production 配置
- 不要自动创建新分支
```

### 5.2 修改指令模板（_instructions/）

修改 `nanobot/templates/agent/_instructions/` 下的 `.md` 文件可以直接改变 AI 的**行为规则**。

例如，要修改"输出规则"，编辑 `output_rules.md` 即可。指令修改立即生效，无需重启进程。

### 5.3 修改框架核心信息（_snippets/）

修改 `nanobot/templates/agent/_snippets/` 下的文件可以改变 AI 理解的框架工作机制。

例如，要修改迭代循环的描述或添加新的工具类型说明，编辑 `framework_core.md`。

### 5.4 在工作区自定义 TOOLS.md

`workspace/TOOLS.md` 是**自动生成**的——框架在每轮对话前重新扫描 `workspace/tools/` 目录并重建索引。用户不应手动编辑此文件。如果要添加工具，将工具脚本放入 `workspace/tools/<tool-name>/` 目录，并编写 `readme.md` 即可。

### 5.5 通过 framework 配置文件调整

`ContextBuilder` 接受 `framework_config` 字典，其中包含的配置项会作为模板变量注入：

```python
framework_config = {
    "max_iterations": 200,           # 最大迭代次数
    "context_window_tokens": 200000, # 上下文窗口大小
    "max_tool_result_chars": 32000,  # 工具结果截断阈值
    "exec_timeout": 60,              # 命令执行超时
    "subagent_max_iterations": 100,  # Subagent 最大迭代次数
    "heartbeat_interval_minutes": 30,# Cron heartbeat 间隔
    "model": "gpt-4",               # 当前模型（注入 identity.md）
    "provider": "openai",            # 当前 provider（注入 identity.md）
}
```

这些配置会通过 `render_template()` 的 kwargs 注入到所有模板中，因此可在任意 `.md` 模板中使用 `{{ max_iterations }}` 等方式引用。

---

## 6. 高级用法：自定义 AI 行为和个性

### 6.1 修改 SOUL.md 改变 AI 人格

`SOUL.md` 是 AI 的"灵魂"。通过重写 `workspace/SOUL.md`，你可以完全自定义 AI 的人格：

```markdown
# Soul - workspace/SOUL.md

我是你的技术助手，工作风格以精准和效率为核心。

## 与我相处的感觉
- 我会先确认需求再开始工作
- 我会主动汇报进度，不等你问
- 你觉得我的建议不合理时可以直接说

## 我说话的节奏
- 技术问题专业详细，日常对话简洁明了
- 重点用要点形式呈现，方便快速扫描

## 价值观
- 准确性优先于速度
- 代码质量优先于功能数量
- 透明决策，记录所有重要选择
```

### 6.2 通过 RULES.md 添加行为规则

当 MemoryExtractor 检测到 conversation 中用户给出了明确的指令性反馈（instruction 类型），这些规则会被写入 `workspace/framework/RULES.md`。

RULES.md 的内容通过 `build_instructions_section()` **每轮自动注入**到上下文中，AI 无需通过 `memory_search` 搜索即可看到。

用户也可**直接编辑** `workspace/framework/RULES.md` 来添加持久化规则。每条规则应包含明确的触发条件和行为指令：

```
- 当修改 build.gradle 文件时，必须同步更新 versions.catlog 中的对应版本号
- 禁止将 API key 硬编码在代码中，必须通过环境变量注入
- 在提交代码前，必须运行 gradlew check 确保编译通过
```

### 6.3 创建自定义 Agent 提示词

模板目录中的多个完整 prompt（如 `assess_me.md`、`evaluator.md`、`extractor_*.md`）都是独立子 agent 的完整 prompt。你可以基于这些模板创建新的子 agent prompt：

1. 在 `nanobot/templates/agent/` 下新建 `.md` 文件
2. 使用 Jinja2 语法编写 prompt
3. 在代码中调用 `render_template("agent/your_template.md", **kwargs)` 渲染

### 6.4 添加新的指令片段

为系统增加新的行为规则：

1. 在 `nanobot/templates/agent/_instructions/` 下新建 `your_rule.md`
2. 在 `context.py` 的 `build_instructions_section()` 中，将 `your_rule` 添加到 `snippet_names` 列表
3. 或者在 system prompt 模板中用 `{% include %}` 包含

---

## 附录 A：加载流程图

```
build_messages()
│
├─ build_system_prompt()
│   ├─ _get_identity()                       → render_template("agent/identity.md")
│   │   └─ 注入OS/路径/模型/资源/配置信息
│   │
│   ├─ _build_tools_section()                → 生成工具签名列表
│   │
│   ├─ _rebuild_tools_index()                → 更新 workspace/TOOLS.md
│   │
│   ├─ _load_bootstrap_files()               → 加载 SOUL.md + USER.md + TOOLS.md
│   │   └─ 优先级：workspace 文件 > 内置模板
│   │
│   └─ render_template("agent/system_prompt.md")
│       └─ {% include 'agent/_snippets/framework_core.md' %}
│
├─ _build_memory_section()                   → 记忆注入
│   ├─ working.md（短期工作记忆）
│   ├─ system.md / user.md（持久配置）
│   └─ events/（最近7天事件）
│
├─ build_instructions_section()              → 指令注入（user消息之前）
│   ├─ Core Rule（核心规则，始终存在）
│   ├─ RULES.md（MemoryExtractor 提取的指令）
│   ├─ agent/_instructions/*.md（各个指令文件）
│   ├─ Always Skills（常驻技能）
│   ├─ Available Skills（按需加载技能摘要）
│   ├─ Task Tree（任务树 + 当前上下文）
│   └─ Team Board（项目事实板）
│
└─ 组装最终 messages[]
    ├─ role: system  ← sys_static + session_parts
    ├─ role: user/assistant/tool  ← 历史消息
    └─ role: user    ← instructions + 用户输入
```
