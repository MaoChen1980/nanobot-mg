# Tool Usage Notes

## Self-Installed Tools (workspace/tools/)

> 💡 **Write your own tools — it's easy and powerful.** Use `write_file` → `then_check="auto"` → `then_exec` to create Python/JS scripts in `workspace/tools/` in one turn. They're instantly usable via `exec` thereafter. Self-written tools are often better than shell commands because you control the output format, error handling, and avoid OS-specific quoting issues.

> ⚠ Document each tool you install below. Future agent instances will read this table and use the tools without rediscovering them.

| Tool | Command | Notes |
|------|---------|-------|
| Python analysis | `python tools/analyze.py <cmd> [args]` | Example: imports, exports, callers, callees, tree, find |
| TS analysis | `node tools/analyze.js <cmd> [args]` | Example: imports, exports, callers, callees, tree, find |
| Fast find | `node tools/fast-find.js <symbol> [dir]` | Example: regex symbol search in .ts files |

⚠ All paths relative to CWD; absolute paths also accepted.

## Known Failures — Don't Repeat

> ⚠ This section is a template. Every time you discover a dead-end approach, document it here to prevent the agent from retrying it across sessions. **The table rows below are template examples — replace them with real failures. Do not avoid these commands unless you've actually hit these errors.**
>
> <!-- Template examples — replace with actual project-specific failures -->
| Date | What | Why failed | Verdict |
|------|------|-----------|---------|
| (date) | `corepack enable` | EPERM — needs admin | ❌ |
| (date) | `npm install -g <pkg>` | EPERM on system dir | ❌ Use `--prefix tools` |
| (date) | `node -e "..."` on CMD | Quote mangling | ❌ Use `write_file`→`exec` |
