# 定时任务系统 (Cron)

## 概述

NanoBot 内置了一个轻量级定时任务系统，用于在指定时间或按固定间隔自动执行 Agent 任务。

核心组件：

- **CronService** (`nanobot/cron/service.py`) — 调度引擎，负责加载、管理、触发任务
- **CronJob / CronSchedule** (`nanobot/cron/types.py`) — 任务与调度的类型定义
- **CronTool** (`nanobot/agent/tools/cron.py`) — 供 AI Agent 在对话中管理任务的工具
- **CronStore** — 持久化存储，任务保存为 JSON 文件

### 工作原理

1. `CronService` 启动时从磁盘加载所有任务，计算每个任务的下次执行时间
2. 使用 `asyncio` 定时器等待最近一个任务到期，到期后执行该任务
3. 任务执行完成后重新计算下次时间，并重置定时器
4. 最大休眠间隔为 5 分钟（`max_sleep_ms=300000`），即使没有任何待执行任务也不会无限空转

### 数据存储

任务数据存储在 workspace 下的 `cron/jobs.json` 文件中：

```
<workspace>/cron/jobs.json   — 任务持久化存储
<workspace>/cron/action.jsonl — 进程间操作日志（用于多进程场景）
```

store 文件路径通过 `CronService(store_path)` 传入，框架中默认为 `<workspace_path>/cron/jobs.json`。

---

## 配置文件中的定时任务

### 系统内置任务

NanoBot 在启动时自动注册以下系统任务，这些任务为 `system_event` 类型，受保护不可删除：

| 任务 ID | 说明 | 默认调度 | 配置字段 |
|---------|------|----------|----------|
| `extractor` | 记忆提取器，定期处理对话历史、提取记忆 | 每 0.5 小时 | `agents.defaults.extractor` |
| `log_check` | 日志检查，定期扫描错误日志 | 每 2 小时 | — |
| `daily-self-review` | 每日自我审视，分析性能数据与改进方向 | 每天 4:00 | `agents.defaults.self_review` |

### 记忆提取器 (Extractor)

```yaml
agents:
  defaults:
    extractor:
      interval_h: 0.5        # 间隔（小时），最小 0.5
      # cron: "0 */2 * * *"  # 可选：用 cron 表达式覆盖 interval_h
      save_interval: 30       # 每 N 轮对话保存一次 .pt 文件
```

- 默认每 30 分钟执行一次
- 可通过 `cron` 字段设置 cron 表达式覆盖基于间隔的调度
- 时间戳基于 `agents.defaults.timezone` 指定的时区

### 每日自我审视 (Self-Review)

```yaml
agents:
  defaults:
    self_review:
      channel: "proxy:feishu:feishu1"   # 交付结果的频道
      to: "chat_xxxx"                    # 交付目标的 ID
      session_key: "..."                 # 会话 key
```

- 每天 UTC 时间 4:00 执行（对应北京时间 12:00）
- 如果配置了 `channel` 和 `to`，审视结果会推送到指定频道
- 未配置时仅记录到日志

### 时区设置

系统时区在 `agents.defaults.timezone` 中配置：

```yaml
agents:
  defaults:
    timezone: "Asia/Shanghai"   # IANA 时区名称
```

- 默认自动检测（Windows 下通过注册表映射，Linux/macOS 下通过系统配置）
- 检测失败时回退为 `"UTC"`
- Windows 常用时区名（如 "China Standard Time"）会被自动映射到 IANA 名称（如 "Asia/Shanghai"）

---

## Cron 表达式格式

cron 表达式由 5 个字段组成，用空格分隔：

```
┌───────── 分钟 (0-59)
│ ┌───────── 小时 (0-23)
│ │ ┌───────── 日 (1-31)
│ │ │ ┌───────── 月 (1-12)
│ │ │ │ ┌───────── 星期 (0-7, 0 和 7 都表示周日)
│ │ │ │ │
* * * * *
```

### 特殊字符

| 字符 | 含义 | 示例 |
|------|------|------|
| `*` | 所有值 | `* * * * *` 每分钟 |
| `,` | 枚举多个值 | `0,30 * * * *` 每小时的第 0 和 30 分 |
| `-` | 范围 | `0 9-17 * * *` 每天 9 点到 17 点每小时 |
| `/` | 步长 | `*/5 * * * *` 每 5 分钟 |

### 常见示例

| 表达式 | 含义 |
|--------|------|
| `0 9 * * *` | 每天早上 9:00 |
| `0 9,18 * * *` | 每天早上 9:00 和下午 18:00 |
| `*/30 * * * *` | 每 30 分钟 |
| `0 */2 * * *` | 每 2 小时整点 |
| `0 9 * * 1-5` | 工作日（周一至周五）早上 9:00 |
| `30 4 * * 0` | 每周日凌晨 4:30 |

### 时区说明

- 使用 `cron` 类型调度时，可指定时区（`tz` 参数）
- 未指定时区时默认使用 `agents.defaults.timezone` 或 `"UTC"`
- 时区仅对 `cron` 类型的调度有效，`every` 和 `at` 类型不支持 `tz` 参数

---

## 支持的任务类型

### 调度类型 (CronSchedule)

每个任务关联一个调度计划，通过 `kind` 字段区分三种模式：

#### 1. `at` — 一次性执行

在指定时间点执行一次，执行后自动删除（或禁用）。

```python
CronSchedule(kind="at", at_ms=1740000000000)
```

- `at_ms`：毫秒级时间戳
- 执行后 `delete_after_run=True` 的任务会被完全移除
- `delete_after_run=False` 的任务在执行后自动禁用（`enabled=False`）
- 服务启动时已过期的任务（超过 60 秒）会被自动清理

#### 2. `every` — 固定间隔重复

按固定时间间隔重复执行。

```python
CronSchedule(kind="every", every_ms=3600000)  # 每小时
```

- `every_ms`：间隔毫秒数
- 每次执行完成后重新计算下次时间（当前时间 + 间隔）
- 适用于 minutes=30 以上的记忆提取器任务

#### 3. `cron` — Cron 表达式调度

使用标准 cron 表达式定义复杂调度规则。

```python
CronSchedule(kind="cron", expr="0 9 * * *", tz="Asia/Shanghai")
```

- `expr`：5 字段 cron 表达式
- `tz`：可选的 IANA 时区（如 `"Asia/Shanghai"`、`"America/New_York"`）
- 依赖 `croniter` 库计算下次执行时间
- 最灵活的调度方式

### 任务类型 (CronPayload)

| 字段 | 类型 | 说明 |
|------|------|------|
| `kind` | `"agent_turn"` \| `"system_event"` | 任务类型 |
| `message` | `str` | 触发时执行的指令 |
| `deliver` | `bool` | 是否将结果推送到用户频道 |
| `channel` | `str \| None` | 目标频道（如 `"whatsapp"`） |
| `to` | `str \| None` | 目标用户/群聊 ID |
| `channel_meta` | `dict` | 频道特定路由信息（如 Slack thread_ts） |
| `session_key` | `str \| None` | 会话 key，用于正确的会话记录 |

- **`agent_turn`**：普通用户任务，AI 触发时会用 message 作为指令与用户交互
- **`system_event`**：系统内部任务（如 `extractor`、`log_check`），受保护不可删除/修改

### 任务状态 (CronJobState)

| 字段 | 说明 |
|------|------|
| `next_run_at_ms` | 下次执行时间戳 |
| `last_run_at_ms` | 上次执行时间戳 |
| `last_status` | `"ok"`、`"error"` 或 `"skipped"` |
| `last_error` | 上次错误信息 |
| `run_history` | 最近 20 次执行记录列表 |

---

## AI 通过工具创建 Cron 任务

AI Agent 通过内置的 `cron` 工具（`CronTool`）管理定时任务。支持 5 种操作：

### 参数一览

| 参数 | 类型 | 说明 |
|------|------|------|
| `action` | `"add"` \| `"list"` \| `"remove"` \| `"update"` \| `"test"` | 操作类型 |
| `name` | `str` | 任务标签（可选，如 `"weather-monitor"`） |
| `message` | `str` | 触发时 AI 执行的指令（action=add 时必须） |
| `every_seconds` | `int` | 间隔秒数 |
| `cron_expr` | `str` | cron 表达式（如 `"0 9 * * *"`） |
| `at` | `str` | ISO 8601 时间（如 `"2026-02-12T10:30:00"`） |
| `tz` | `str` | IANA 时区（仅与 cron_expr 搭配使用） |
| `deliver` | `bool` | 是否推送结果到用户频道（默认 `true`） |
| `job_id` | `str` | 任务 ID（action=remove/update/test 时需要） |
| `dry_run` | `bool` | 测试时是否静默执行（不发送消息） |

### 创建任务

通过 `action=add` 创建任务，需提供 `message` 和一种调度方式：

```
cron action=add name="天气提醒" message="查一下今天北京的天气并告诉我" cron_expr="0 8 * * *" tz="Asia/Shanghai"
cron action=add message="提醒我做伸展运动" every_seconds=3600
cron action=add message="半小时后提醒我开会" at="2026-02-12T10:30:00"
```

- `every_seconds`、`cron_expr`、`at` 三选一，不能同时使用
- `tz` 只能与 `cron_expr` 搭配使用
- 使用 `at` 创建的一次性任务执行后自动删除
- 新建的任务 ID 自动生成（8 位 uuid 前缀）

### 列出任务

```
cron action=list
```

输出示例：

```
Scheduled jobs:
- 天气提醒 (id: a1b2c3d4, cron: 0 8 * * * (Asia/Shanghai))
  Last run: 2026-02-12 08:00:00 CST — ok
  Next run: 2026-02-13 08:00:00 CST
- extractor (id: extractor, every 0.5h)
  Purpose: System-managed internal job.
  Protected: visible for inspection, but cannot be removed.
```

### 更新任务

```
cron action=update job_id=a1b2c3d4 message="新的提醒内容" every_seconds=7200
cron action=update job_id=a1b2c3d4 name="重命名" cron_expr="0 9 * * *" tz="Asia/Shanghai"
```

- 只更新传入的字段，不传入的字段保持不变
- 系统任务（`system_event`）受保护，无法更新

### 删除任务

```
cron action=remove job_id=a1b2c3d4
```

- 系统任务受保护，无法删除
- 常规任务可随时删除

### 测试任务

```
cron action=test job_id=a1b2c3d4
cron action=test job_id=a1b2c3d4 dry_run=true   # 静默测试，不发送消息到频道
```

- 立即触发一次任务执行，不等待调度时间
- 显示详细的执行步骤日志
- `dry_run=true` 不会向用户频道发送消息

### 任务内操作

在 cron 任务执行过程中，AI Agent 可以通过 `cron` 工具更新或删除当前任务：

```
cron action=update message="更新后的提醒"   # 无需 job_id，自动识别当前任务
cron action=remove                         # 取消当前任务
```

但不允许在 cron 任务执行期间创建新的 cron 任务（会返回错误）。

---

## 查看和管理任务

### 通过对话管理

直接与 AI 对话管理定时任务：

- "帮我看看有什么定时任务" — 等效于 `action=list`
- "帮我删掉天气提醒" — 需要先 list 获取 job_id
- "把每小时的提醒改成每两小时" — 等效于 `action=update`
- "测试一下这个任务" — 等效于 `action=test`

### 数据文件

任务数据以 JSON 格式持久化到 `cron/jobs.json`，可直接查看：

```json
{
  "version": 1,
  "jobs": [
    {
      "id": "a1b2c3d4",
      "name": "天气提醒",
      "enabled": true,
      "schedule": {
        "kind": "cron",
        "expr": "0 8 * * *",
        "tz": "Asia/Shanghai"
      },
      "payload": {
        "kind": "agent_turn",
        "message": "查一下今天北京的天气并告诉我",
        "deliver": true,
        "channel": "whatsapp",
        "to": "861380000000"
      },
      "state": {
        "nextRunAtMs": 1740000000000,
        "lastRunAtMs": 1739913600000,
        "lastStatus": "ok",
        "runHistory": [...]
      },
      "createdAtMs": 1739827200000,
      "updatedAtMs": 1739913600000,
      "deleteAfterRun": false
    }
  ]
}
```

> **注意**：请勿直接编辑 JSON 文件，框架在启动时会重算所有任务的下次执行时间。如果需要修改任务，通过 AI 对话中的 `cron` 工具操作。

---

## 日志和调试

### 日志输出

所有 cron 操作都会通过 `loguru` 记录日志，日志级别为 INFO 及以上：

```
2026-02-12 08:00:00.123 | INFO     | Cron service started with 3 jobs
2026-02-12 08:00:00.456 | INFO     | Cron: executing job '天气提醒' (a1b2c3d4)
2026-02-12 08:00:05.789 | INFO     | Cron: job '天气提醒' completed
2026-02-12 08:00:05.790 | INFO     | Cron: added job '每日检查' (e5f6g7h8)
2026-02-12 08:00:05.791 | INFO     | Cron: removed job e5f6g7h8
```

失败重试日志：

```
2026-02-12 08:00:10.123 | WARNING  | Cron: job '网络检查' failed (attempt 1/3), retrying in 5s: ConnectionError
2026-02-12 08:00:15.456 | WARNING  | Cron: job '网络检查' failed (attempt 2/3), retrying in 10s: ConnectionError
2026-02-12 08:00:25.789 | ERROR    | Cron: job '网络检查' failed after 3 attempts: ConnectionError
```

### 错误处理机制

- **重试策略**：任务执行失败时自动重试，最多 3 次
- **退避算法**：每次重试等待时间递增（5s, 10s, 20s）
- **持久化保护**：写入文件使用原子写入（先写临时文件，再 rename），防止崩溃导致数据损坏
- **数据恢复**：store 文件损坏时自动备份为 `.corrupt-<timestamp>` 后缀，不会丢失原有数据
- **并发安全**：使用 `FileLock` 和 `action.jsonl` 操作日志支持多进程安全读写

### 测试任务

使用 `action=test` 可以对任务进行一键测试，无需等待调度时间：

```
cron action=test job_id=a1b2c3d4 dry_run=true
```

测试模式下：
- 不向用户频道发送消息（`dry_run=true`）
- 显示执行步骤流水
- 返回执行结果预览（前 200 字符）
- 不修改任务的调度状态

### 获取服务状态

通过 `CronService.status()` 可以获取服务运行状态：

| 字段 | 说明 |
|------|------|
| `enabled` | 服务是否运行中 |
| `jobs` | 任务总数 |
| `next_wake_at_ms` | 下次唤醒时间戳 |

---

## API 参考

### cron 模块公开接口

```python
from nanobot.cron import CronService, CronJob, CronSchedule
```

### CronService 方法

| 方法 | 说明 |
|------|------|
| `start()` | 启动调度服务 |
| `stop()` | 停止调度服务 |
| `add_job(name, schedule, message, ...)` | 添加新任务 |
| `remove_job(job_id)` | 删除任务 |
| `update_job(job_id, ...)` | 更新任务 |
| `enable_job(job_id, enabled)` | 启用/禁用任务 |
| `get_job(job_id)` | 获取单个任务 |
| `list_jobs(include_disabled)` | 列出所有任务 |
| `run_job(job_id, force)` | 手动触发执行 |
| `register_system_job(job)` | 注册系统任务（幂等） |
| `status()` | 获取服务状态 |
