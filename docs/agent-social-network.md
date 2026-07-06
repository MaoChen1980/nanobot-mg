# 子代理社交网络

NanoBot 的子代理（Subagent）机制允许主代理创建（spawn）多个子代理并行处理任务。子代理之间可以互相通信，也可以向主代理（Orchestrator）报告进度和结果。这种多代理协作模式构成了一个"社交网络"般的协作体系。

## 架构概览

```
┌─────────────────────────────────────────┐
│          Main Agent (Orchestrator)        │
│     (AgentLoop / process_direct)         │
│           ↑ ↓ 通信  ↑ ↓                 │
│  ┌───────┐ ┌───────┐ ┌───────┐           │
│  │ Sub-1 │←→│ Sub-2 │←→│ Sub-3 │ ...      │
│  └───────┘ └───────┘ └───────┘           │
└─────────────────────────────────────────┘
```

核心组件：

- **SubagentManager** — 子代理管理器的中枢
- **SubagentStatus / SubagentResult** — 状态追踪与结果结构化
- **Team Communication Tools** — NotifyOrchestratorTool, TellSubagentTool
- **SubagentHook** — 子代理生命周期的钩子
- **build_subagent_tools** — 子代理工具的构建（不含 spawn 能力）
- **build_subagent_prompt** — 子代理系统提示的构建

---

## SubagentManager

位于 [subagent.py](../nanobot/agent/subagent.py) 的 `SubagentManager` 类：

```python
class SubagentManager:
    def __init__(self, provider, workspace, bus, ...):
        self._spawn_semaphore = asyncio.Semaphore(max(1, _max_sa))
        self.runner = AgentRunner(provider)
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._task_statuses: dict[str, SubagentStatus] = {}
        self._subagent_inboxes: dict[str, asyncio.Queue[str]] = {}
        ...
```

### 核心 API

| 方法 | 说明 |
|------|------|
| `spawn(task, label, ...)` | 创建一个子代理在后台执行任务 |
| `cancel_by_session(session_key)` | 取消某个会话的所有子代理 |
| `cancel_by_label(label)` | 按标签取消指定子代理 |
| `get_status(task_id)` | 查询子代理状态 |
| `list_running_statuses()` | 列出所有正在运行的子代理 |
| `send_to_subagent(label, content)` | 主代理向子代理发送消息 |
| `notify_orchestrator(message, ...)` | 子代理向主代理发送通知 |

### Spawn 流程

1. 生成唯一 `task_id`（UUID 前 8 位）
2. 创建 `SubagentStatus` 追踪实时状态
3. 创建 `asyncio.Queue` 作为子代理的收件箱
4. 创建后台 `asyncio.Task` 执行 `_run_subagent`
5. 注册 done callback 进行清理（移除状态、收件箱等映射）
6. 返回给主代理一条确认消息

---

## 子代理通信机制

### 主代理 → 子代理

每个子代理持有一个 `asyncio.Queue[str]` 作为收件箱。主代理通过 `send_to_subagent(label, content)` 向指定子代理发送消息：

```python
async def _drain_inbox(*, limit: int = 10) -> list[dict[str, Any]]:
    items = []
    while len(items) < limit:
        try:
            text = inbox.get_nowait()
            items.append({"role": "user", "content": text})
        except asyncio.QueueEmpty:
            break
    return items
```

该 `_drain_inbox` 作为 `injection_callback` 传入 `AgentRunner.run()`，在每轮迭代前被调用，将收件箱中的消息注入为 user 消息。

### 子代理 → 主代理

子代理通过 `notify_orchestrator()` 向主代理发送通知。该方法：

1. 构造一条 `InboundMessage`，channel 设为 `"system"`
2. 通过 `MessageBus.publish_inbound()` 发布到总线
3. 主代理在下一轮迭代中会收到该消息并处理

```python
async def notify_orchestrator(self, message, subagent_id, subagent_label, priority="info"):
    origin = self._subagent_origin.get(subagent_id)
    await self._inject_to_orchestrator(
        f"[Subagent '{subagent_label}' ({priority})]: {message}",
        origin,
        metadata={"injected_event": "subagent_notification", ...},
    )
```

### 子代理 ←→ 子代理

通过 TellSubagentTool 实现。该工具持有 SubagentManager 引用：

```python
tools.register(TellSubagentTool(manager=self, subagent_id=task_id, subagent_label=label))
```

子代理在提示中可以看到其他活跃子代理的列表（通过 `list_running_statuses()`），并可以向它们发送消息：

- 发送方使用 `TellSubagentTool` 写入目标子代理的收件箱
- 目标子代理在下一轮迭代中通过 `_drain_inbox` 读取

---

## 生命周期管理

```
  spawn()  ──→  initializing
                    │
                    ▼
               awaiting_tools  ←── AgentRunner 循环
                    │
                    ▼
              tools_completed
                    │
                    ▼
              final_response
                    │
           ┌────────┴────────┐
           ▼                  ▼
         done              error / timeout
```

### 状态数据类

[subagent_status.py](../nanobot/agent/subagent_status.py) 定义了：

```python
@dataclass(slots=True)
class SubagentStatus:
    task_id: str
    label: str
    phase: str = "initializing"  # initializing | awaiting_tools | tools_completed | final_response | done | error
    iteration: int = 0
    tool_events: list = []
    usage: dict = {}
    error: str | None = None

@dataclass(slots=True)
class SubagentResult:
    task_id: str
    status: str  # "ok" | "error" | "needs_review"
    final_content: str | None
    duration_s: float
    token_usage: dict
    ...
```

### 结果通告

子代理完成后，`_announce_result()` 使用模板 `agent/subagent_announce.md` 生成结果消息，并通过 `_inject_to_orchestrator()` 注入给主代理。

---

## 并发控制

```python
self._spawn_semaphore = asyncio.Semaphore(max(1, _max_sa))
# _max_sa 来自环境变量 NANOBOT_MAX_SUBAGENTS，默认 5
```

所有子代理的执行（`_run_subagent`）受信号量控制，避免过多子代理同时消耗 LLM 资源。

---

## 子代理的工具集

[subagent_tools.py](../nanobot/agent/subagent_tools.py) 的 `build_subagent_tools()` 为子代理构建工具集：

```python
def build_subagent_tools(workspace, web_config, exec_config, ...):
    tools = ToolRegistry()
    # 文件系统：ReadFileTool, WriteFileTool, EditFileTool, ...
    # 搜索：GlobTool, GrepTool, SearchTextTool
    # Web：WebFetchTool, WebSearchTool
    # Shell：ExecTool
    # 内存：MemorySearchTool, ConversationSearchTool
    # 分析：AnalyzeTool, DebugRootCauseTool
    # 检查点：SaveCheckpointTool, ListCheckpointsTool, RestoreCheckpointTool
    # 自我评估：AssessMeTool
    # 注意：没有 spawn 能力（无 SubagentManager）
    return tools
```

关键区别：子代理**不能**创建子代理（`_in_subagent` context variable 会阻止嵌套）。

---

## 自我评估与根因分析

子代理每 10 轮迭代运行一次 `assess_me` 自我评估：

1. 调用 LLM 分析当前进度并返回 JSON
2. 检查是否有 blocker（阻塞点）
3. 如有 blocker，自动调用 `debug_root_cause` 根因分析
4. 将评估结果注入消息列表，引导子代理自我修正

```python
assess_me_callback=_assess_callback,
assess_interval=10,
```
