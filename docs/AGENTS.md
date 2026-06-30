# NanoBot 代理系统

NanoBot 的代理系统由五个核心组件构成，协同实现从消息输入到智能回复的完整处理流水线。

## 架构概览

```
消息输入
    │
    ▼
┌─────────────────────────────────────────────┐
│              AgentLoop (loop.py)              │
│  消息路由、会话管理、命令解析、子代理编排     │
│                                              │
│  ┌──────────┐  ┌──────────┐ ┌────────────┐  │
│  │ AgentRunner │  │ Context  │ │   Hook    │  │
│  │ (runner.py) │  │ Builder  │ │ (hook.py) │  │
│  └──────┬───────┘  └──────────┘ └────────────┘  │
│         │                                         │
│         ▼                                         │
│  ┌──────────────┐                                │
│  │ Skill Loader │ (skills.py)                     │
│  └──────────────┘                                │
│         │                                         │
│         ▼                                         │
│    工具执行 → LLM 调用 → 结果回复                  │
└─────────────────────────────────────────────┘
    │
    ▼
消息输出
```

---

## AgentLoop（主循环）

位于 [loop.py](../nanobot/agent/loop.py)，是代理系统的核心引擎。

### 职责

- 接收和路由入站消息（`InboundMessage`）
- 管理会话生命周期（`SessionManager`, `SessionLifecycle`）
- 解析斜杠命令（`CommandRouter`）
- 编排子代理（`SubagentManager`）
- 驱动消息处理流程（`process_direct`）
- 检查点与恢复（`RecoveryManager`, checkpoints）
- 消息总线集成（`MessageBus`）

### 核心方法

```python
class AgentLoop:
    async def process_direct(self, content, session_key, channel, chat_id,
                             media=None, metadata=None, on_progress=None,
                             pending_queue=None) -> OutboundMessage | None:
        """
        处理单条用户消息的完整生命周期：
        1. 恢复或创建 Session
        2. 构建系统提示（ContextBuilder）
        3. 注册工具（ToolRegistry）
        4. 调用 AgentRunner.run() 执行 LLM 循环
        5. 持久化记忆（MemoryExtractor）
        6. 返回 OutboundMessage
        """
```

### 检查点系统

支持中断恢复：

```python
from .loop_checkpoint import (
    RecoveryManager,
    set_runtime_checkpoint,
    restore_and_clear_checkpoint,
    mark_pending_user_turn,
    clear_pending_user_turn,
)
```

---

## AgentRunner（执行器）

位于 [runner.py](../nanobot/agent/runner.py)，是工具调用型代理的共享执行循环。

### 职责

- 驱动 LLM 请求-工具执行的迭代循环
- 管理 LLM 调用重试（`BackoffStrategy`, `RetryContext`）
- 注入消息（`injection_callback`）
- 自我评估（`assess_me_callback`）
- 限制迭代次数、Token 使用量

### 核心类型

```python
@dataclass
class AgentRunSpec:
    initial_messages: list[dict]      # 初始消息列表
    tools: ToolRegistry                # 可用工具
    model: str                         # LLM 模型名
    max_iterations: int                # 最大迭代次数
    max_tool_result_chars: int         # 工具结果截断长度
    hook: AgentHook                    # 生命周期钩子
    injection_callback: Callable       # 消息注入回调
    assess_me_callback: Callable       # 自我评估回调
    assess_interval: int               # 评估轮次间隔
    session_key: str | None            # 会话键
    reasoning_effort: str | None       # 推理努力级别
    ...

class AgentRunner:
    async def run(self, spec: AgentRunSpec) -> RunResult:
        """
        执行代理的 LLM 循环，直到：
        - LLM 返回最终回复（无工具调用）
        - 达到最大迭代次数
        - 发生不可恢复错误
        """
```

### 流程

```
Loop:
  1. 调用 injection_callback 注入新消息
  2. 调用 assess_me_callback 进行自我评估
  3. 调用 LLM（LLMProvider.stream/messages）
  4. 解析响应中的工具调用
  5. 执行工具（可能并发）
  6. 记录结果到消息列表
  7. 重复直到终止条件满足
```

### 重试策略

```python
BackoffConfig(
    initial_delay=1.0,   # 初始重试延迟
    multiplier=2.0,      # 指数退避乘数
    max_delay=60.0,      # 最大延迟
    jitter=0.1,          # 抖动因子
)
```

---

## ContextBuilder（上下文管理）

位于 [context.py](../nanobot/agent/context.py)，负责组装代理的系统提示。

### 职责

- 加载 bootstrap 文件（系统提示模板）
- 构建工具定义描述
- 集成技能（skill）内容
- 注入工作区文件上下文
- 处理媒体内容（图片压缩、base64 数据 URL）

### 构建流程

```python
class ContextBuilder:
    def __init__(self, workspace, timezone=None, disabled_skills=None, db=None, project_root=None):
        self.workspace = workspace
        self.timezone = timezone
        self.disabled_skills = disabled_skills or []
        self.db = db
        self.project_root = project_root
        self._skills_loader = SkillsLoader(workspace)
        self._template_cache: dict[str, tuple[float, str]] = {}

    def build(self, tool_definitions, messages=None, memory_context=None,
              tree_data=None, current_task=None, session_key=None,
              instructions_section=None, ...) -> list[dict]:
        """
        组装完整的消息列表：
        1. 加载系统提示模板（bootstrap 文件）
        2. 插入工具定义
        3. 插入技能内容（always-inject skills）
        4. 插入记忆上下文
        5. 插入任务状态
        6. 返回 [system_msg, ...history_messages, ...new_messages]
        """
```

### 缓存机制

- 模板文件缓存（最多 20 个文件，以 mtime 校验）
- 系统信息缓存（内存信息、GPU 信息，每个会话计算一次）
- 工具索引缓存（`rebuild_tools_index`）

---

## Hook 系统

位于 [hook.py](../nanobot/agent/hook.py)，提供可扩展的生命周期钩子。

### 钩子基类

```python
class AgentHook:
    # 运行级钩子
    async def before_run(self, context: AgentRunHookContext)  # 运行前
    async def after_run(self, context: AgentRunHookContext)   # 运行后
    async def on_error(self, context: AgentRunHookContext)    # 出错时
    async def on_finally(self, context: AgentRunHookContext)  # 最终清理

    # 迭代级钩子
    async def before_iteration(self, context: AgentHookContext)  # 每轮 LLM 调用前
    async def after_iteration(self, context: AgentHookContext)   # 每轮 LLM 调用后
    async def before_execute_tools(self, context: AgentHookContext)  # 工具执行前
    async def on_stream(self, context, delta)      # 流式 token
    async def on_stream_end(self, context, *, resuming)  # 流式结束
    async def on_reasoning(self, context, delta)    # 推理 token
    async def on_reasoning_end(self, context)       # 推理结束
    async def after_turn(self)                      # 完整用户轮次结束

    # 管道方法（多个钩子按顺序串联）
    def before_llm_call(self, context, messages) -> list[dict]      # 修改 LLM 消息
    def filter_tool_calls(self, context, tool_calls) -> list         # 过滤工具调用
    def finalize_content(self, context, content) -> str | None      # 最终内容处理
```

### 上下文数据类

```python
@dataclass(slots=True)
class AgentRunHookContext:
    messages: list[dict]           # 完整消息列表
    final_content: str | None      # 最终回复内容
    tools_used: list[str]          # 使用过的工具
    usage: dict[str, int]          # Token 用量
    stop_reason: str | None        # 停止原因
    error: str | None              # 错误信息
    exception: BaseException | None
    metadata: dict                 # 扩展元数据

@dataclass(slots=True)
class AgentHookContext:
    iteration: int                 # 当前迭代轮次
    messages: list[dict]           # 当前消息列表
    response: LLMResponse | None   # LLM 响应
    tool_calls: list               # 当前工具调用
    tool_results: list             # 工具执行结果
    ...
```

### CompositeHook

`CompositeHook` 是钩子的组合器，将多个钩子按顺序串联：

```python
class CompositeHook(AgentHook):
    # 安全执行每个钩子，单个钩子异常不影响其他钩子
    async def _for_each_hook_safe(self, method_name, *args, **kwargs):
        for h in self._hooks:
            try:
                await getattr(h, method_name)(*args, **kwargs)
            except Exception:
                logger.exception(...)
```

### SDKCaptureHook

`SDKCaptureHook` 专为 SDK 消费者设计，捕获工具名、消息、用量和停止原因：

```python
class SDKCaptureHook(AgentHook):
    # 用于 Nanobot.run() 填充 RunResult
    self.tools_used: list[str] = []
    self.messages: list[dict] = []
    self.usage: dict[str, int] = {}
    self.stop_reason: str | None = None
```

---

## 技能系统（Skills）

位于 [skills.py](../nanobot/agent/skills.py)，为代理提供可插拔的能力。

### SkillsLoader

```python
class SkillsLoader:
    def __init__(self, workspace, builtin_skills_dir=None, disabled_skills=None):
        self.workspace = workspace
        self.workspace_skills = workspace / "skills"   # 用户自定义技能
        self.builtin_skills = builtin_skills_dir or BUILTIN_SKILLS_DIR  # 内置技能
        self.disabled_skills = disabled_skills or set()
```

### 技能文件格式

每个技能是一个 Markdown 文件（`SKILL.md`），包含 YAML frontmatter：

```markdown
---
name: my_skill
description: 技能描述
always: false  # true = 每轮注入到系统提示
---

## 技能内容

详细的指令和说明...
```

### 技能目录结构

```
skills/                    # 内置技能目录
├── core/                  # 核心技能
│   ├── SKILL.md
│   └── ...
├── productivity/          # 生产力技能
│   └── SKILL.md
└── code/                  # 编码技能
    └── SKILL.md

workspace/skills/          # 用户工作区自定义技能
└── SKILL.md
```

### 关键特性

- **Frontmatter 解析**：使用正则 `^---\s*\r?\n(.*?)\r?\n---` 提取 YAML
- **always-inject 机制**：标记了 `always: true` 的技能自动注入到每轮系统提示
- **技能缓存**：`_skill_cache: dict[str, tuple[float, str, str]]` 以 mtime 校验
- **列表缓存**：`_list_cache` 在技能目录 mtime 变化时自动失效
- **自我优化**：支持技能文件的自动化验证与优化（通过 skill-manager）

---

## Coding Agent 模式

NanoBot 支持作为 Coding Agent 使用，能够扫描和分析项目代码。

### 项目扫描

通过 `nanobot init` 命令或 `scan_project` 工具触发。

[project_scanner.py](../nanobot/agent/project_scanner.py) 会：

1. 扫描项目目录的实际文件系统内容
2. 自动检测：语言、构建系统、测试框架、CI/CD 配置
3. 识别：入口点、配置文件、依赖管理
4. 生成 `project_card.md` — 结构化的项目卡片，供 AI 在后续操作中参考

```bash
# CLI 方式
nanobot init /path/to/project

# 在 Agent 交互中使用
# AI 可自动调用 scan_project 工具来理解项目
```

### 工作目录模式

通过 `--project-root` 参数，CLI 可以指定项目根目录：

```bash
nanobot agent --project-root /path/to/project
```

此时 ContextBuilder 会：
- 加载 `project_card.md` 作为上下文
- 将工具的文件操作限制在项目目录内
- 使用 Coding Agent 角色的提示词模板

---

## 组件协作流程

```
1. 消息到达 AgentLoop.process_direct()
2. 恢复 Session（SessionManager）
3. ContextBuilder 构建系统提示：
   a. 加载 bootstrap 模板
   b. SkillsLoader 注入 always-inject 技能
   c. 插入工具定义
   d. 注入记忆上下文、任务状态
4. ToolRegistry 注册所有可用工具
5. AgentRunner.run() 开始 LLM 迭代循环：
   a. 注入新消息（injection_callback）
   b. 自我评估（assess_me_callback）
   c. Hook.before_iteration → LLM 调用 → Hook.after_iteration
   d. 解析工具调用 → Hook.before_execute_tools → 执行工具
   e. 循环直到 LLM 返回纯文本回复
6. MemoryExtractor 提取并持久化记忆
7. Hook.after_turn
8. 返回 OutboundMessage
```
