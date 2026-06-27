## Available Skills

以下 skills 扩展了你的能力。**当用户输入匹配某个 skill 的描述时，必须优先加载该 skill**——用 `read_file` 阅读其 SKILL.md 并按步骤执行。
不可用的 skills 需要先安装依赖——你可以尝试用 apt/brew/pip 安装。

每个 skill 包含 **When to Use**（何时加载）、**Steps**（执行步骤）、**Verification**（成功标准）。
执行后务必对照 Verification 章节检查。不满足则说明 skill 需要更新——**此时必须使用 skill-manager 进行修复**。

{{ skills_summary }}
