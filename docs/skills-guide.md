# NanoBot 技能系统指南

## 1. 技能系统概述

技能（Skill）是 NanoBot 中**扩展 AI 代理能力的预定义指令包**。每个技能是一个包含 `SKILL.md` 文件的目录，其中以结构化 Markdown 形式描述了代理在特定场景下应遵循的执行步骤、工具用法和验证标准。

**技能的作用：**

- **封装领域知识** — 将天气查询、代码审查、GitHub 操作等特定领域的操作步骤固化为可复用的指令。
- **提高执行确定性** — 通过明确的 Step → Verification 流程，减少 LLM 的随意性，保证任务质量。
- **自我进化** — 技能可以通过 skill-manager 进行自我修复和优化，当步骤错误或发现更好的方法时自动更新。
- **即插即用** — 内置技能开箱即用，用户可自由创建自定义技能扩展代理能力。

技能系统源自 [OpenClaw](https://github.com/openclaw/openclaw) 的规范，保持了格式和元数据结构的兼容性。

---

## 2. 技能目录结构

技能分两类来源：

### 内置技能（Builtin Skills）

```
nanobot/skills/
├── code-review/              # 代码审查
├── weather/                  # 天气查询
├── cron/                     # 定时任务
├── skill-manager/            # 技能管理（自我进化的核心）
├── summarize/                # 内容总结
├── github/                   # GitHub 操作
├── plan/                     # 任务规划
├── excalidraw/               # Excalidraw 图表
├── architecture-diagram/     # 架构图生成
├── codebase-inspection/      # 代码库规模分析
├── stock-analyzer/           # 股票分析
├── imap-smtp-email/          # 邮件收发
├── apple/                    # Apple/macOS 生态技能
│   ├── imessage/             #   iMessage 收发
│   ├── apple-notes/          #   Apple Notes 操作
│   ├── apple-reminders/      #   Apple 提醒事项
│   └── findmy/               #   Find My 定位
│   └── macos-computer-use/   #   macOS 桌面操控
└── ...                       # 其他数十个技能
```

### 工作区技能（Workspace Skills）

位于工作区目录（默认 `~/.nanobot/workspace/skills/`）下，结构与内置技能完全相同。工作区技能优先级高于内置技能——同名技能会覆盖内置版本。

---

## 3. 技能文件格式

每个技能目录必须包含 `SKILL.md` 文件，由 **YAML frontmatter** 和 **Markdown 正文**两部分组成。

### Frontmatter 结构

```yaml
---
name: skill-name              # 必填。连字符命名法，小写，与目录名一致
category: domain-specific     # 必填。hyphen-case 分类标识
description: >                # 必填。三段式触发描述
  功能概述。
  当用户[场景1]、[场景2]时，必须使用此 Skill。
  关键词：[关键词1]、[关键词2]。
  即使用户没有明确说'[术语]'，只要涉及[相关概念]，都应触发。
always: false                 # 可选。是否每轮注入（默认为 false）
version: 0.1.0                # 可选。版本号
license: MIT                  # 可选。许可证
metadata:                     # 可选。扩展元数据
  nanobot:                    #   nanobot 特定配置
    always: true              #     在 metadata 中设置 always
    tags: [tag1, tag2]        #     标签
    related_skills: [skill]   #     关联技能
platforms: [linux, macos, windows]  # 可选。支持平台
---
```

**description 字段的关键性：**

description 是 LLM 判断何时加载该技能的**唯一触发信号**。它必须使用三段式格式：

1. **功能概述** — 一句话说明技能做什么
2. **触发场景** — 列举用户说什么/做什么时触发，末尾加"必须使用此 Skill"
3. **关键词 + 隐含触发** — 让不精确的描述也能正确匹配

### 正文结构

每个 `SKILL.md` 正文必须包含以下核心章节，按顺序排列：

| 章节 | 用途 | 说明 |
|------|------|------|
| `## When to Use` | 触发条件 | 明确什么场景下加载此技能 |
| `## Steps` | 执行步骤 | 编号步骤，包含确切的命令、代码或操作流程 |
| `## Verification` | 验证标准 | 执行后对照此处判断成功/失败，格式为可执行的检查项 |
| `## Pitfalls` | 边界情况 | 已知问题、操作系统差异、常见错误 |

此外，每个技能末尾必须包含**自我优化脚注**（由 `SkillsLoader` 自动添加）：

```
- **Self-optimization**: 此 Skill 可自我进化。
  - Verification 未通过 → 加载 skill-manager 修复
  - Verification 全部通过 → 加载 skill-manager 优化
```

### 辅助文件与子目录

技能目录下允许创建以下子目录：

```
skills/<name>/
├── SKILL.md          # 必填
├── scripts/          # 可执行脚本（Python、Shell、Node.js 等）
├── references/       # 参考文档（API 文档、配色表、示例等）
└── assets/           # 模板、字体、图片等静态资源
```

不允许子目录：`README.md`、`INSTALLATION_GUIDE.md`、`CHANGELOG.md` 等文件不应出现在技能目录中。

### 验证工具

使用内置验证脚本检查技能结构：

```bash
python nanobot/skills/skill-manager/scripts/quick_validate.py nanobot/skills/<name>
```

验证项包括：
- Frontmatter 格式有效（YAML 可解析）
- `name` 与目录名一致
- `description` 非空且不含占位符
- `category` 存在且为合法的 hyphen-case
- `always` 为布尔值（如果存在）
- 仅包含允许的子目录

---

## 4. 内置技能概览

NanoBot 内置了数十个技能，可按 `category` 分类如下（基于 `skill-manager/scripts/list_categories.py` 动态枚举）：

### Code Analysis（代码分析）
| 技能 | 描述 |
|------|------|
| `code-review` | 审查代码中的 bug、安全问题、代码坏味和维护性问题 |
| `codebase-inspection` | 用 pygount 分析代码库的代码行数、语言分布和代码/注释比 |
| `codegraph` | 代码库图谱分析（PR 分析、模式识别、bug 分析） |
| `bugfix` | 修复 bug 的标准操作流程 |
| `simplify-code` | 简化复杂代码的逻辑 |

### Domain-specific（领域特定）
| 技能 | 描述 |
|------|------|
| `weather` | 通过 wttr.in 和 Open-Meteo 查询天气 |
| `github` | 使用 gh CLI 管理 PR、Issue、CI、代码搜索 |
| `summarize` | 总结 URL、网页、本地文件、YouTube 视频 |
| `stock-analyzer` | 综合股票分析（实时行情、基本面、技术指标、投资评级） |
| `imap-smtp-email` | 通过 IMAP/SMTP 收发邮件（支持主流邮箱服务商） |
| `daily-trending` | 获取每日热门趋势 |
| `cron` | 管理定时任务和提醒 |
| `assess-me` | 代理自我评估与改进 |

### Design（设计）
| 技能 | 描述 |
|------|------|
| `architecture-diagram` | 生成深色主题的技术架构 SVG 图（HTML 文件） |
| `excalidraw` | 生成手绘风格的 Excalidraw JSON 图表（架构图、流程图等） |
| `canvas-design` | Canvas 设计工具（含字体资源包） |
| `diagram-maker` | 通用图表制作工具 |

### Agent（代理管理）
| 技能 | 描述 |
|------|------|
| `skill-manager` | 技能库的增删改查，技能自我进化的核心工具 |
| `skill-vetter` | 技能质量审查 |
| `plan` | 规划模式：编写可执行的 Markdown 计划 |
| `delegate` | 任务委派 |

### Communication（通信）
| 技能 | 描述 |
|------|------|
| `imessage`（`apple/imessage`） | 通过 macOS Messages.app 收发 iMessage/SMS |
| `apple-reminders` | 管理 Apple 提醒事项 |
| `apple-notes` | 操作 Apple Notes |
| `findmy` | Apple Find My 定位 |

### Productivity（生产力）
| 技能 | 描述 |
|------|------|
| `google-workspace` | Google Workspace 集成（Gmail 搜索等） |
| `notion` | Notion 集成（块类型参考） |
| `nano-pdf` | PDF 处理 |
| `maps` | 地图与位置服务 |
| `ocr-and-documents` | OCR 和文档提取 |

### Project Management（项目管理）
| 技能 | 描述 |
|------|------|
| `cron` | 定时调度 |
| `plan` | 项目规划 |
| `github-pr-workflow` | PR 工作流管理 |
| `github-issues` | Issue 管理模板 |
| `github-repo-management` | 仓库管理 |

### 跨平台技能
许多技能通过 `platforms` 字段声明支持的平台：如 `weather` 跨平台，`imessage` 仅 macOS，`codebase-inspection` 全平台。

---

## 5. 如何创建自定义技能

### 步骤

**1. 确认必要性**

创建技能前确认：
- 有明确的**外部触发条件**（用户关键词、工具返回、cron 周期）
- 任务涉及**多步骤工作流**（5 次以上 tool call）
- 方法**不明显**，值得记录复用
- 不是 LLM 已经能自然完成的简单任务

**2. 检查重复**

使用 `memory_search` 语义检索已有技能，确认不重复。

**3. 创建目录与文件**

```bash
mkdir -p workspace/skills/<skill-name>/
```

**4. 编写 SKILL.md**

使用以下模板：

```markdown
---
name: <skill-name>
category: <category>
description: >
  [功能概述]。
  当用户[场景1]、[场景2]、[场景3]时，必须使用此 Skill。
  关键词：[关键词1]、[关键词2]。
  即使用户没有明确说'[精确术语]'，只要涉及[相关概念]，都应触发。
always: false
---

# <Skill Name>

## When to Use

- 用户场景 1
- 用户场景 2

## Steps

1. **步骤一**：具体操作
2. **步骤二**：具体操作

## Verification

- 检查项 1
- 检查项 2

## Pitfalls

- 边界情况 1
- 边界情况 2
```

**5. 验证**

```bash
python nanobot/skills/skill-manager/scripts/quick_validate.py workspace/skills/<skill-name>
```

**6. 确认索引中可见**

运行以下命令确认技能出现在技能索引中，且 description 足够触发正确匹配：

```bash
python -c "from nanobot.agent.skills import SkillsLoader; from pathlib import Path; print(SkillsLoader(Path('workspace')).build_skills_summary())"
```

### 设计原则

| 原则 | 说明 |
|------|------|
| **Trigger → Action → Goal** | 没有触发条件的技能不应被创建。每个技能必须有明确的外部触发信号 |
| **Progressive Disclosure** | SKILL.md 控制在 500 行以内，详细内容移至 `references/` |
| **具体 > 抽象** | "Add User model with email and password_hash fields" 而非 "Add authentication" |
| **可验证** | Verification 章节必须写具体的 success criteria（exit code、文件存在、关键字出现） |

### 命名规范

| 好 | 差 |
|----|----|
| `github-pr-workflow` | `github` |
| `pdf-processing` | `pdf` |
| `data-science-pipeline` | `ds` |

- 使用连字符命名法，全小写，仅含字母和数字
- 名称暗示技能的功能范围

---

## 6. 技能的加载和注入机制

NanoBot 的技能加载由 `nanobot/agent/skills.py` 中的 `SkillsLoader` 类管理，支持两种注入模式：

### 6.1 Always-inject（始终注入）

当一个技能的 frontmatter 中设置 `always: true`（或在 `metadata.nanobot.always` 中设置）时，该技能会在**每一轮对话**中自动注入到代理的指令区域（instructions section），紧邻生成点附近。

```yaml
---
name: my-essential-skill
always: true
---
```

实现机制（[context.py](file:///e:/claude/nanobot-mg/nanobot/agent/context.py#L365-L375)）：

```
always_skills_names = self.skills.get_always_skills()
always_content = self.skills.format_skills_for_context(always_skills_names)
```

注意：
- always-inject 技能出现在 `## Active Skills` 区域
- 被 always-inject 的技能会从下方的 `### Available Skills` 摘要中排除，避免重复
- 建议仅在技能的行为尚未成为 LLM 自然倾向时使用 `always: true`，一旦成为自然倾向应改为 `false`

### 6.2 按需调用（On-demand）

默认情况下，技能不会主动注入。代理在指令区的 `### Available Skills` 中看到所有可用技能的描述摘要（category 分组折叠格式）。

当用户输入匹配某个技能的 `description` 时，代理应通过文件读取工具加载该技能的完整 `SKILL.md` 并按照步骤执行。

实现机制（[context.py](file:///e:/claude/nanobot-mg/nanobot/agent/context.py#L377-L385)）：

```
skills_summary = self.skills.build_skills_summary(exclude=set(always_skills_names))
```

`build_skills_summary` 方法会：
1. 扫描内置技能目录和工作区技能目录
2. 解析每个 SKILL.md 的 frontmatter 获取 category 和 description
3. 按 category 分组，支持 `<details>` 折叠展示
4. 检查依赖（CLI 工具、环境变量）是否满足，不满足的技能标记为 unavailable
5. 排除已 always-inject 的技能

### 6.3 技能索引注入模板

技能摘要通过模板 `nanobot/templates/agent/skills_section.md` 注入：

```
## Available Skills

以下 skills 扩展了你的能力。当用户输入匹配某个 skill 的描述时，
必须优先加载该 skill——用 read_file 阅读其 SKILL.md 并按步骤执行。
不可用的 skills 需要先安装依赖——你可以尝试用 apt/brew/pip 安装。

每个 skill 包含 When to Use（何时加载）、Steps（执行步骤）、
Verification（成功标准）。执行后务必对照 Verification 章节检查。
不满足则说明 skill 需要更新——此时必须使用 skill-manager 进行修复。

{{ skills_summary }}
```

### 6.4 自我优化脚注

`SkillsLoader` 在加载技能时，会自动检查并添加自我优化脚注到 SKILL.md 末尾：

```
- **Self-optimization**: 此 Skill 可自我进化。
  - Verification 未通过 → 加载 skill-manager 修复
  - Verification 全部通过 → 加载 skill-manager 优化
  - **Always 审查**：如果此 skill 的行为已成为 LLM 自然倾向，
    将 frontmatter 的 `always: true` 改为 `false`
```

对于工作区技能，此修改会自动通过 git 提交。

### 6.5 技能加载优先级

1. **工作区技能 > 内置技能** — 同名技能时，工作区版本覆盖内置版本
2. **不可用技能自动过滤** — 依赖的 CLI 工具或环境变量不满足时，技能不会被注入
3. **缓存机制** — `SkillsLoader` 通过文件 mtime 缓存技能内容，避免重复 I/O

---

## 7. 在配置中启用/禁用技能

### 禁用技能

在 NanoBot 配置文件中，通过 `disabled_skills` 字段禁用不需要的技能：

```yaml
agents:
  defaults:
    disabled_skills:
      - summarize
      - weather
      - stock-analyzer
```

实现（[schema.py](file:///e:/claude/nanobot-mg/nanobot/config/schema.py#L182)）：

```python
disabled_skills: list[str] = Field(default_factory=list)
# Skill names to exclude from loading (e.g. ["summarize", "skill-manager"])
```

### 生效机制

当 `ContextBuilder` 初始化时，将 `disabled_skills` 传递给 `SkillsLoader`：

```python
self.skills = SkillsLoader(workspace, disabled_skills=set(disabled_skills))
```

在 `SkillsLoader._refresh_skills_list()` 中，禁用的技能会被过滤掉：

```python
if self.disabled_skills:
    skills = [s for s in skills if s["name"] not in self.disabled_skills]
```

禁用后，该技能将：
- 不出现在 `build_skills_summary()` 的列表中
- 不会被 `get_always_skills()` 纳入
- 无法通过 `load_skill()` 或 `format_skills_for_context()` 加载

### subagent 的禁用继承

Subagent 的禁用技能列表由主代理传递：

```python
build_subagent_prompt(
    workspace,
    disabled_skills=disabled_skills,
    ...
)
```

即主代理中禁用的技能，在子代理中同样不可用。

---

## 附录 A：技能生命周期

```
创建 → 验证 → 注册（放入 skills/ 目录）
                          ↓
              自动出现在 skills_summary 中
                          ↓
          用户触发 → 代理加载 SKILL.md 执行
                          ↓
            Verification 不通过 → skill-manager 修复
            Verification 通过 → 可选优化
                          ↓
            behavior becomes natural → 设 always=false
            needs per-round presence → 设 always=true
                          ↓
              技能过时 → 手动删除或禁用
```

## 附录 B：文件参考

| 文件 | 说明 |
|------|------|
| `nanobot/agent/skills.py` | 技能加载器核心实现 |
| `nanobot/agent/context.py` | 技能注入到上下文（always-inject + summary） |
| `nanobot/agent/subagent_prompt.py` | Subagent 的技能注入 |
| `nanobot/config/schema.py` | `disabled_skills` 配置字段 |
| `nanobot/templates/agent/skills_section.md` | 技能摘要注入模板 |
| `nanobot/skills/README.md` | 技能目录总览 |
| `nanobot/skills/skill-manager/scripts/quick_validate.py` | 技能结构验证脚本 |
| `nanobot/skills/skill-manager/scripts/init_skill.py` | 技能脚手架工具 |
| `nanobot/skills/skill-manager/scripts/list_categories.py` | 列出已有分类 |
