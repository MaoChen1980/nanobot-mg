# #soul Rules

## Self-Modification

更新 SOUL.md、USER.md、MEMORY.md 时的纪律：

- **WHEN** 修改 SOUL.md → **THEN** 确保 tag 分类完整、Core 循环不变
- **WHEN** 新增 tag → **THEN** 同步创建对应的 `memory/rules/<tag>.md` 并在 MEMORY.md 索引中添加
- **WHEN** 修改规则 → **THEN** 先 `git_inspect(log=<file>)` 审查历史，避免重复已被否决的方案

## MemoryExtractor

- 自动从对话提取 findings → `memory/` 文件
- 自动重建 FAISS 索引
- 核对 SOUL.md/USER.md 中的矛盾、重复、过期内容
