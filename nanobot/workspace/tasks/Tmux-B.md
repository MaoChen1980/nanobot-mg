# TmuxSessionManager — 创建报告

## Summary

成功创建 `TmuxSessionManager` 及相关配套组件（ShellSessionEntity, ShellSessionDao），并修改 ProcessManager 暴露 binPath/bashPath 属性。

---

## Files Created

### 1. `TmuxSessionManager.kt` (287 lines, 8755 bytes)

**位置**: `app/src/main/kotlin/com/mobileaiagent/agent/shell/TmuxSessionManager.kt`

**核心功能**:
- `createSession(workingDir)` — 创建持久化 bash 会话
- `sendCommand(sessionId, command, timeoutSeconds)` — 向会话发送命令并等待结果
- `listSessions()` — 列出所有活跃/分离的会话
- `detachSession(sessionId)` — 分离会话（保持进程运行）
- `killSession(sessionId)` — 终止会话并清理资源
- `shutdown()` — 关闭时清理所有会话

**内部数据结构**:
- `SessionInfo` — 会话信息（sessionId, pid, workingDir, status, createdAt, lastActiveAt）
- `CommandResult` — 命令结果（stdout, stderr, exitCode, timedOut）

**关键设计**:
- 使用 `ConcurrentHashMap<String, Process>` 管理活跃进程
- 使用 `ConcurrentHashMap<String, OutputStreamWriter>` 管理 stdin 写入器
- 命令执行使用轮询 + delay方式读取输出（超时控制）
- 通过 Room `ShellSessionDao` 持久化会话元数据

---

### 2. `ShellSessionEntity.kt` (17 lines, 432 bytes)

**位置**: `app/src/main/kotlin/com/mobileaiagent/data/local/entity/ShellSessionEntity.kt`

```kotlin
@Entity(tableName = "shell_sessions")
data class ShellSessionEntity(
    @PrimaryKey val sessionId: String,
    val pid: Int,
    val workingDir: String,
    val status: String = "ACTIVE",
    val createdAt: Long = System.currentTimeMillis(),
    val lastActiveAt: Long = System.currentTimeMillis(),
)
```

---

### 3. `ShellSessionDao.kt` (44 lines, 1452 bytes)

**位置**: `app/src/main/kotlin/com/mobileaiagent/data/local/dao/ShellSessionDao.kt`

**接口方法**:
- `insert(session)` — 插入新会话
- `update(session)` — 更新会话
- `updateStatus(sessionId, status, lastActiveAt)` — 更新状态（ACTIVE/DETACHED）
- `touch(sessionId, lastActiveAt)` — 更新 lastActiveAt 时间戳
- `kill(sessionId)` — 删除会话记录
- `getActiveSessions()` — 获取活跃会话列表（suspend）
- `getActiveSessionsFlow()` — 获取活跃会话 Flow
- `getById(sessionId)` — 按 ID 获取会话
- `deleteAll()` — 删除所有会话

---

## Files Modified

### 4. `ProcessManager.kt` —暴露 binPath/bashPath

**改动**:
```kotlin
// 原始:
private val binPath: String,  // Directory containing bash, busybox, etc.

// 修改后:
private val _binPath: String,  // Directory containing bash, busybox, etc.

// 新增公开属性:
val binPath: String get() = _binPath
val bashPath: String get() = "$binPath/bash"
```

**验证**:
```
grep "val binPath: String get() = _binPath" ProcessManager.kt
→ E:/claude/mobile-ai-agent/app/src/main/kotlin/com/mobileaiagent/agent/shell/ProcessManager.kt:26

grep "val bashPath: String get() = \"$binPath/bash\"" ProcessManager.kt
→ E:/claude/mobile-ai-agent/app/src/main/kotlin/com/mobileaiagent/agent/shell/ProcessManager.kt:27
```

---

### 5. `AppDatabase.kt` — 添加 ShellSessionEntity 和 ShellSessionDao

**改动**:
1. 新增 import：`ShellSessionDao`, `ShellSessionEntity`
2. `@Database` annotation: `entities` 添加 `ShellSessionEntity::class`，`version` 从 3 升到 4
3. 新增 abstract method: `abstract fun shellSessionDao(): ShellSessionDao`
4. Migration: `MIGRATION_2_3` → `MIGRATION_3_4`，逻辑改为创建 `shell_sessions` 表

---

## grep 验证结果

|验证项 | Pattern | 结果 |
|--------|---------|------|
| TmuxSessionManager 类定义 | `class TmuxSessionManager` | ✅ 找到 (line 29) |
| TmuxSessionManager 引用 | `TmuxSessionManager` | ✅ 找到 (lines 17, 29) |
| ProcessManager binPath | `val binPath: String get() = _binPath` | ✅ 找到 (line 26) |
| ProcessManager bashPath | `val bashPath: String get() = "$binPath/bash"` | ✅ 找到 (line 27) |

---

## 使用示例

```kotlin
// 在 ViewModel 或 Service 中注入
val processManager = ProcessManager(binPath, sandboxPath)
val shellSessionDao = AppDatabase.getInstance(context).shellSessionDao()
val tmuxManager = TmuxSessionManager(processManager, shellSessionDao, sandboxPath)

// 创建新会话
val session = tmuxManager.createSession("/data/local/tmp")

// 发送命令
val result = tmuxManager.sendCommand(session.sessionId, "ls -la", timeoutSeconds = 30)
println("stdout: ${result.stdout}")
println("exitCode: ${result.exitCode}")

// 列出所有会话
val sessions = tmuxManager.listSessions()

// 终止会话
tmuxManager.killSession(session.sessionId)

// 清理（应用退出时）
tmuxManager.shutdown()
```

---

## 注意事项

1. **Android 限制**: 会话不跨应用重启存活（Android 不允许后台持久进程）
2. **长任务**: 需要长时间运行的任务应使用 WorkManager 或 Foreground Service
3. **超时处理**: `sendCommand` 使用轮询方式检测命令完成，在高延迟环境可能不准确
4. **并发安全**: 使用 `ConcurrentHashMap` 保证线程安全

---

## 依赖关系

```
TmuxSessionManager
  ├── ProcessManager (提供 bashPath, binPath)
  └── ShellSessionDao (持久化会话元数据)
        └── AppDatabase (Room 数据库)
              └── ShellSessionEntity
```