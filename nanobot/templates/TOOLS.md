## Creating New Tools

你可以通过编写自己的 tools 来扩展 nanobot 的能力。每个 tool 独立存放在 `workspace/tools/` 下的子目录中。

### How to create a tool

1. 创建目录：`workspace/tools/<tool-name>/`
2. 编写脚本（Python、shell、bat 或任何可执行格式）
3. 编写 `readme.md` 描述使用方法

### readme.md format

```markdown
# Tool Name — one-line description

## Usage
    python workspace/tools/<name>/script.py <arg1> <arg2>

## Arguments
- `arg1`: description
- `arg2`: description

## Examples
    python workspace/tools/<name>/script.py --input file.txt

## Dependencies
List any required packages or system dependencies.
```

### How to use installed tools

- 本文件顶部的 **Installed Tools** 部分为自动生成，每轮自动刷新
- 阅读 tool 的 `readme.md` 了解如何调用
- 使用 `exec`（shell 执行）运行 tool 脚本
- 如果 tool 有无法安装的依赖，将其添加到 readme 并请求帮助

### Maintenance — Self-Healing & Updates

当 tool 在使用中报错时，进行调查并修复：

1. 使用调试标志运行 tool 或检查错误输出
2. 阅读 tool 的脚本理解出错原因
3. 用 `edit_file` 或 `write_file` 修复脚本
4. 如果接口（参数、输出格式）发生变化，更新其 `readme.md`

增强 tool 时，始终保持 readme.md 同步：

- 参数变了？更新 **Arguments** 部分
- 增加了功能？更新 **Examples**
- 如果 tool 已过时，删除其目录——索引会在下一轮自动清理

### Best practices

- 每个目录一个 tool —— 专注、单一用途的脚本
- 包含错误处理和清晰的输出
- 在 readme.md 中记录参数和示例
- 脚本中尽量支持 `--help` 标志
