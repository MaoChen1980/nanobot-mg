## 任务
检查 memory 目录下知识文件的质量问题：矛盾、过时、重复。

## 输出要求

只输出以下 JSON 格式，不要多余文字：

```json
{
  "suggestions": [
    {
      "file": "SOUL.md",
      "action": "remove|rewrite|keep",
      "reason": "conflicts with '<quote from other part>' on line N",
      "target_text": "exact text to modify",
      "replacement": "new text (rewrite only)"
    }
  ]
}
```

## 输入文件

你将收到一个或多个文件的完整内容。除了 `{{ workspace_path }}/SOUL.md` 和 `{{ workspace_path }}/USER.md`，可能还会收到主题文件（如 `Python/build.md`、`Project/nanobot.md` 等）。所有收到的文件都可以修改。

## 检查项

### 1. CONTRADICTION — 直接冲突的陈述
示例："对所有项目使用 Python 3.11" vs. "Python 3.12 是默认版本"
→ `rewrite`：删除或纠正错误的那条

### 2. OUTDATED — 被同一文件中的其他内容直接矛盾的内容
→ `remove`：删除过时的陈述

### 3. DUPLICATE — 同一含义、措辞几乎相同
→ `remove`：保留一份

### 4. VAGUE — 表述模糊，缺乏可操作的具体信息
"要注意错误处理"、"尽量用好的做法"这类空泛建议 → `remove`：没有具体内容的知识比没有知识更糟——它占用注意力但不提供指导
具体性判断：是否包含具体的工具名、路径、模式名、命令、配置项？如无 → 疑似空泛

### 5. IRRELEVANT — 与当前工作无关的知识
- 该项目已经不再使用的技术栈相关记忆
- 仅在历史上下文中成立、当前已不相关的决策
→ `remove`：过时上下文

### 6. SPECULATIVE — 推测性内容，非确定事实
- "可能是这样"、"看起来像是"、"不确定但" 这类表述
- 没有经过验证的假设被记录为知识
→ `remove`：推测不是知识

## 约束

- **保守与清理的平衡**：宁可漏报不可误报，但"模糊"和"推测"两条可以放宽——模糊知识占用的注意力成本高于它可能带来的价值
- `target_text` 必须是待修改的确切文本——至少一整行，足够唯一标识该范围
- `replacement` 是 `rewrite` 操作的必填项，`remove` 操作省略
- `reason` 必须引用冲突/重复的对应内容，以便人工验证
- 不要标记格式、风格或仅仅是"可以更好"的内容
- 如果无需修改，返回 `"suggestions": []`
