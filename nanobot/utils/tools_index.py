"""Self-installed tools index — scans workspace/tools/*/ and generates TOOLS.md."""

from __future__ import annotations

from pathlib import Path

from importlib.resources import files as pkg_files
from loguru import logger


_GUIDE_SECTION = """\
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

- The index above is auto-generated and refreshed on every turn
- Read a tool's `readme.md` to learn how to invoke it
- Use `exec` (shell execution) to run the tool script
- If the tool has dependencies you can't install, add them to the readme and ask for help

### Maintenance — Self-Healing & Updates

When a tool errors during use, investigate and fix it:

1. Run the tool with debugging flags or check the error output
2. Read the tool's script to understand what went wrong
3. Fix the script with `edit_file_tool` or `write_file_tool`
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
"""


def init_tools_dir(workspace: Path) -> Path:
    """Ensure workspace/tools/ exists. Returns the tools directory path."""
    tools_dir = workspace / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    return tools_dir


def rebuild_tools_index(workspace: Path) -> None:
    """Scan workspace/tools/*/ for installed tools and regenerate workspace/TOOLS.md.

    Reads each tool's readme.md to build the index, then writes the complete
    TOOLS.md with index + guide. Called automatically on every system prompt build.
    """
    tools_dir = workspace / "tools"

    entries: list[dict[str, str]] = []
    if tools_dir.is_dir():
        for child in sorted(tools_dir.iterdir()):
            if not child.is_dir():
                continue
            tool_name = child.name
            readme = child / "readme.md"
            description = _readme_first_heading(readme) if readme.is_file() else "No description"
            entries.append({"name": tool_name, "description": description})

    parts: list[str] = ["# Tool Usage Notes"]

    if entries:
        lines = ["## Installed Tools\n"]
        for e in entries:
            lines.append(f"- **{e['name']}**: {e['description']} — `workspace/tools/{e['name']}/`")
        parts.append("\n".join(lines))

    parts.append(_guide_section())
    output = "\n\n".join(parts) + "\n"

    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "TOOLS.md").write_text(output, encoding="utf-8")


def _readme_first_heading(path: Path) -> str:
    """Extract the first heading or first non-empty line from a readme file."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        logger.debug("Failed to read first heading from {}", path)
        return "No description"

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped.lstrip("# ").strip()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return "No description"


def _guide_section() -> str:
    """Load the guide section from bundled template, falling back to constant."""
    try:
        tpl = pkg_files("nanobot") / "templates" / "TOOLS.md"
        if tpl.is_file():
            content = tpl.read_text(encoding="utf-8")
            guide = content.strip()
            if guide:
                return guide
    except Exception:
        logger.debug("Failed to load guide section template")
    return _GUIDE_SECTION.strip()
