# 工具自动验证设计

## 原则

原子操作（读/写/移动/删除文件、执行命令等）执行结果可直接验证，无需 LLM 传参判断成功失败。

## 验证模式

### 1. 文件系统原子操作

| 工具 | 验证内容 |
|------|----------|
| `write_file` | 内容首行 grep 验证（then_grep 自动注入） |
| `edit_file` | 内容首行 grep 验证（then_grep 自动注入） |
| `delete_file` | 框架层检查文件不存在 → ✓/⚠️ |
| `move_file` | 目标文件存在 + 源文件消失 → ✓/⚠️ |

### 2. Shell 执行

| 工具 | 验证内容 |
|------|----------|
| `exec` | exit code = 0 → ✓，非 0 → ❌ |
| `exec` (from_cache) | exit code → ✓/❌ 标记 |

### 3. 复合工具

| 工具 | 验证内容 |
|------|----------|
| `message` | media 文件存在检查（发送前预验证） |
| `cron` test | 结果含 ✅/❌ + error 摘要 |
| `notebook_edit` | "Successfully" 标记 |

### 4. 已有验证（无需改）

- `read_file` → 内容非空
- `list_dir` → 返回条目数
- `grep` → 匹配结果 + exit code ✓/❌
- `glob` → 文件列表
- `web_search` → 结果列表
- `web_fetch` → 内容非空 + untrusted banner
- `memory_search` / `conversation_search` → 结果列表（混合搜索）
- `recall` → 结果非空

### 5. 外部代理（无法自动验证）

| 工具 | 处理方式 |
|------|----------|
| `mcp_*` | 依赖外部系统返回 error 标记 |
| `spawn` | 后台异步，结果按需检查 |

## LLM 传参验证（补充层）

框架自动验证是零成本的基础保障，LLM 可通过参数进一步控制：

- `then_grep` → 自定义内容验证模式
- `then_check="auto"` → 写完自动运行 pyright/tsc 语法检查
- `then_exec` → 写完自动执行脚本

## 实施记录

| 日期 | 改动 |
|------|------|
| 2026-05-10 | `delete_file` 加文件不存在验证 |
| 2026-05-10 | `move_file` 加目标存在 + 源消失验证 |
| 2026-05-10 | `shell/exec` exit code 加 ✓/❌ 标记 |
| 2026-05-10 | `message` 加 media 文件存在预检查 |