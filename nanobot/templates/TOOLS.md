## Creating New Tools

You can extend nanobot's capabilities by writing your own tools. Each tool lives in its own subdirectory under `workspace/tools/`.

### How to create a tool

1. Create a directory: `workspace/tools/<tool-name>/`
2. Write your script (Python, shell, bat, or any executable format)
3. Write a `readme.md` describing how to use it

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

- The **Installed Tools** section at the top of this file is auto-generated and refreshed on every turn
- Read a tool's `readme.md` to learn how to invoke it
- Use `exec` (shell execution) to run the tool script
- If the tool has dependencies you can't install, add them to the readme and ask for help

### Maintenance — Self-Healing & Updates

When a tool errors during use, investigate and fix it:

1. Run the tool with debugging flags or check the error output
2. Read the tool's script to understand what went wrong
3. Fix the script with `edit_file` or `write_file`
4. If the interface (args, output format) changed, update its `readme.md`

When enhancing a tool, always keep readme.md in sync:

- Changed arguments? Update the **Arguments** section
- Added features? Update **Examples**
- If a tool becomes obsolete, delete its directory — the index cleans up automatically on the next turn

### Best practices

- One tool per directory — focused, single-purpose scripts
- Include error handling and clear output
- Document arguments and examples in readme.md
- Use `--help` flag support in your scripts when possible
