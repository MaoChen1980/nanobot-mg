"""Explore a code module — extract structure overview in one call."""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema
from nanobot.agent.tools.filesystem.filesystem_base import _FsTool

_IGNORE_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache", ".ruff_cache"})

# -- language-specific definition patterns --
# Each entry: (regex_pattern, kind) where kind is "class", "function", or "decorator"
_LANG_PATTERNS: dict[str, list[tuple[str, str]]] = {
    ".py": [
        (r"^class\s+(\w+)\s*(?:\(.*?\))?\s*:", "class"),
        (r"^async\s+def\s+(\w+)\s*\(.*?\)\s*(?:->.*?)?:", "function"),
        (r"^def\s+(\w+)\s*\(.*?\)\s*(?:->.*?)?:", "function"),
    ],
    ".js": [
        (r"^(?:export\s+)?(?:default\s+)?class\s+(\w+)", "class"),
        (r"^(?:export\s+)?function\s+(\w+)\s*\(", "function"),
        (r"^(?:export\s+)?(?:async\s+)?function\s*(\w+)\s*\(", "function"),
        (r"^const\s+(\w+)\s*=\s*(?:async\s*)?\(.*?\)\s*=>", "function"),
        (r"^const\s+(\w+)\s*=\s*(?:async\s*)?function", "function"),
    ],
    ".ts": [
        (r"^(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(\w+)", "class"),
        (r"^(?:export\s+)?(?:abstract\s+)?(?:async\s+)?function\s+(\w+)\s*\(", "function"),
        (r"^(?:export\s+)?interface\s+(\w+)", "class"),
        (r"^(?:export\s+)?type\s+(\w+)\s*=", "class"),
        (r"^(?:export\s+)?(?:default\s+)?const\s+(\w+)\s*:\s*(?:Async\s*)?\(", "function"),
        (r"^const\s+(\w+)\s*=\s*(?:async\s*)?\(.*?\)\s*=>", "function"),
    ],
    ".go": [
        (r"^func\s+(?:\(.*?\)\s+)?(\w+)\s*\(", "function"),
        (r"^type\s+(\w+)\s+struct", "class"),
        (r"^type\s+(\w+)\s+interface", "class"),
    ],
    ".rs": [
        (r"^(?:pub\s+)?(?:unsafe\s+)?(?:async\s+)?fn\s+(\w+)", "function"),
        (r"^(?:pub\s+)?(?:unsafe\s+)?trait\s+(\w+)", "class"),
        (r"^(?:pub\s+)?(?:unsafe\s+)?struct\s+(\w+)", "class"),
        (r"^(?:pub\s+)?(?:unsafe\s+)?enum\s+(\w+)", "class"),
        (r"^(?:pub\s+)?(?:unsafe\s+)?impl\s+(\w+)", "class"),
    ],
    ".java": [
        (r"^(?:public|private|protected)\s+(?:static\s+)?(?:final\s+)?(?:class|interface|enum|record)\s+(\w+)", "class"),
        (r"^(?:public|private|protected)\s+(?:static\s+)?(?:final\s+)?(?:synchronized\s+)?(?:[\w<>[\],?.\s]+)\s+(\w+)\s*\(", "function"),
    ],
    ".kt": [
        (r"^(?:public|private|protected|internal)?\s*(?:open|abstract|data|sealed|inner)?\s*(?:class|interface|object|enum class)\s+(\w+)", "class"),
        (r"^(?:public|private|protected|internal)?\s*(?:suspend\s+)?(?:override\s+)?(?:inline\s+)?fun\s+(\w+)", "function"),
    ],
}


@tool_parameters(
    build_parameters_schema(
        path=p("string", "Absolute path to a file or directory to explore."),
        max_level=p("integer", "Maximum depth for directory listing (default 3, max 5). Depth=1 lists top-level; depth=3 shows subdirectories 3 levels deep.", minimum=1, maximum=5, default=3),
        show_refs=p("boolean", "Show a sample of internal references for each symbol (default true)", default=True),
    ),
    required=["path"],
)
class ExploreModuleTool(_FsTool):
    """Get a bird's-eye view of a code module — classes, functions, their signatures and line numbers."""

    name = "explore_module_tool"
    read_only = True

    description = (
        "**Purpose**: Get a structured overview of a code file or directory (function/class definitions, signatures, line numbers).\n\n"
        "**Output format**:\n"
        "- Function and class definitions with 1-indexed line numbers (directly usable as read_file_tool offset param)\n"
        "- Python uses AST parsing (precise), other languages use regex (may be incomplete)\n"
        "- Appends a read_file_tool tip command ready to copy-paste\n\n"
        "**When to use**:\n"
        "- When you want a quick overview of what classes and functions exist in a file or directory and where they are defined\n\n"
    )

    async def execute(
        self,
        path: str = "",
        max_level: int = 3,
        show_refs: bool = True,
        **kwargs: Any,
    ) -> str:
        try:
            fp = self._resolve(path)
            if not fp.exists():
                return f"Error: Path not found: {path}"
            if fp.is_file():
                return self._explore_file(fp)
            if fp.is_dir():
                return self._explore_directory(fp, max_level)
            return f"Error: Not a file or directory: {path}"
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error exploring module: {e}"

    # ------------------------------------------------------------------
    # File exploration
    # ------------------------------------------------------------------

    def _explore_file(self, fp: Path) -> str:
        suffix = fp.suffix.lower()
        try:
            raw = fp.read_bytes()
        except OSError as e:
            return f"Error reading {fp.name}: {e}"

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return f"(Binary file, cannot explore: {fp.name})"

        lines = text.replace("\r\n", "\n").split("\n")
        total_lines = len(lines)

        if suffix == ".py":
            result = self._explore_python(fp, text, lines)
        else:
            result = self._explore_generic(fp, suffix, lines)

        result += f"\n({total_lines} lines total in {fp.name})"

        # Actionable hint: tell LLM how to jump to definitions
        result += (
            f"\n\n- Tip: use `read_file_tool(path=\"{fp.resolve().as_posix()}\", offset=<line_no>, limit=40)` "
            f"to read a specific function/class body."
        )
        return result

    # ------------------------------------------------------------------
    # Python AST-based exploration
    # ------------------------------------------------------------------

    def _explore_python(self, fp: Path, text: str, lines: list[str]) -> str:
        try:
            tree = ast.parse(text, filename=str(fp))
        except SyntaxError as e:
            return f"Error: could not parse {fp.name} — SyntaxError: {e}"

        parts: list[str] = [f"# {fp.name}"]
        symbols: list[str] = []

        if ast.get_docstring(tree):
            doc = ast.get_docstring(tree).split("\n")[0]
            parts.append(f"'''{doc}'''")

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            block = self._render_python_node(node, lines, symbols, indent=0)
            if block:
                parts.append(block)

        if symbols:
            parts.append("")
            parts.append("-- cross-references (first 30) --")
            for sym in symbols[:30]:
                refs = self._find_refs(text, sym)
                if refs:
                    parts.append(f"  {sym}: {', '.join(refs[:5])}")

        return "\n\n".join(parts)

    def _render_python_node(
        self, node: ast.AST, lines: list[str], symbols: list[str], indent: int = 0
    ) -> str | None:
        pad = "  " * indent
        if isinstance(node, ast.ClassDef):
            symbols.append(node.name)
            bases = []
            for base in node.bases:
                if isinstance(base, ast.Name):
                    bases.append(base.id)
                elif isinstance(base, ast.Attribute):
                    bases.append(self._format_attr(base))
                elif isinstance(base, ast.Call) and isinstance(base.func, ast.Name):
                    bases.append(f"{base.func}(...)")
            base_str = f"({', '.join(bases)})" if bases else ""
            decorators = self._get_decorators(node, lines)
            parts = [f"{pad}class {node.name}{base_str}:  # line {node.lineno}"]
            if decorators:
                for deco in reversed(decorators):
                    parts.insert(0, f"{pad}  {deco}")
            if ast.get_docstring(node):
                doc = ast.get_docstring(node).split("\n")[0]
                parts.append(f"{pad}  '''{doc}'''")

            for child in ast.iter_child_nodes(node):
                child_block = self._render_python_node(child, lines, symbols, indent + 1)
                if child_block:
                    parts.append(child_block)

            return "\n".join(parts)

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(node.name)
            prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
            args = self._format_args(node.args)
            returns = self._format_return_annotation(node) if node.returns else ""
            decorators = self._get_decorators(node, lines)
            parts = [f"{pad}{prefix}def {node.name}({args}){returns}:  # line {node.lineno}"]
            if decorators:
                for deco in reversed(decorators):
                    parts.insert(0, f"{pad}  {deco}")
            if ast.get_docstring(node):
                doc = ast.get_docstring(node).split("\n")[0]
                parts.append(f"{pad}  '''{doc}'''")
            return "\n".join(parts)

        return None

    @staticmethod
    def _get_decorators(node: ast.AST, lines: list[str]) -> list[str]:
        """Extract decorator names from source lines."""
        deco_lines = []
        if hasattr(node, "decorator_list"):
            for deco in node.decorator_list:
                if isinstance(deco, ast.Name):
                    deco_lines.append(f"{deco.id}")
                elif isinstance(deco, ast.Attribute):
                    parts = []
                    curr = deco
                    while isinstance(curr, ast.Attribute):
                        parts.append(curr.attr)
                        curr = curr.value
                    if isinstance(curr, ast.Name):
                        parts.append(curr.id)
                    deco_lines.append(".".join(reversed(parts)))
                elif isinstance(deco, ast.Call) and isinstance(deco.func, ast.Name):
                    deco_lines.append(f"{deco.func.id}(...)")
                elif isinstance(deco, ast.Call):
                    curr = deco.func
                    if isinstance(curr, ast.Attribute):
                        parts = []
                        while isinstance(curr, ast.Attribute):
                            parts.append(curr.attr)
                            curr = curr.value
                        if isinstance(curr, ast.Name):
                            parts.append(curr.id)
                        deco_lines.append(".".join(reversed(parts)))
        return [f"@{d}" for d in deco_lines]

    @staticmethod
    def _format_args(args: ast.arguments) -> str:
        parts: list[str] = []
        if args.posonlyargs:
            parts.extend(a.arg for a in args.posonlyargs)
            parts.append("/")
        all_args = args.args
        start = 1 if (all_args and all_args[0].arg in ("self", "cls")) else 0
        parts.extend(a.arg for a in all_args[start:])
        if args.vararg:
            parts.append(f"*{args.vararg.arg}")
        if args.kwonlyargs:
            if not args.vararg:
                parts.append("*")
            parts.extend(a.arg for a in args.kwonlyargs)
        if args.kwarg:
            parts.append(f"**{args.kwarg.arg}")
        return ", ".join(parts)

    @staticmethod
    def _format_return_annotation(node: ast.FunctionDef) -> str:
        ann = node.returns
        if isinstance(ann, ast.Name):
            return f" -> {ann.id}"
        if isinstance(ann, ast.Constant) and isinstance(ann.value, str):
            return f" -> {ann.value}"
        if isinstance(ann, ast.Subscript):
            return " -> [...]"
        if isinstance(ann, ast.Attribute):
            parts = []
            curr = ann
            while isinstance(curr, ast.Attribute):
                parts.append(curr.attr)
                curr = curr.value
            if isinstance(curr, ast.Name):
                parts.append(curr.id)
            return f" -> {'::'.join(reversed(parts))}"
        return ""

    @staticmethod
    def _format_attr(node: ast.Attribute) -> str:
        parts = []
        curr = node
        while isinstance(curr, ast.Attribute):
            parts.append(curr.attr)
            curr = curr.value
        if isinstance(curr, ast.Name):
            parts.append(curr.id)
        return ".".join(reversed(parts))

    def _find_refs(self, text: str, name: str) -> list[str]:
        refs: list[str] = []
        pattern = re.compile(rf'\b{re.escape(name)}\b')
        seen = set()
        for match in pattern.finditer(text):
            pos = match.start()
            line_start = text.rfind("\n", 0, pos) + 1 if pos > 0 else 0
            line_end = text.find("\n", pos)
            line = text[line_start:text.find("\n", pos)].strip() if line_end != -1 else text[line_start:].strip()
            if not line:
                continue
            if line.lstrip().startswith(("def ", "class ", "async def ")):
                continue
            key = line[:60]
            if key in seen:
                continue
            seen.add(key)
            refs.append(line[:80])
        return refs

    # ------------------------------------------------------------------
    # Generic (regex-based) exploration for non-Python files
    # ------------------------------------------------------------------

    def _explore_generic(self, fp: Path, suffix: str, lines: list[str]) -> str:
        if suffix not in _LANG_PATTERNS:
            return f"Error: unsupported file type '{suffix}' for {fp.name}"
        patterns = _LANG_PATTERNS[suffix]
        text = "\n".join(lines)

        found: list[tuple[int, str, str]] = []
        for pattern, kind in patterns:
            for i, line in enumerate(lines, 1):
                m = re.search(pattern, line)
                if m:
                    sig = line.strip()
                    if len(sig) > 120:
                        sig = sig[:117] + "..."
                    found.append((i, kind, sig))

        if not found:
            return f"(No class/function definitions found in {fp.name})"

        found.sort(key=lambda x: x[0])
        classes = [(l, s) for l, k, s in found if k == "class"]
        functions = [(l, s) for l, k, s in found if k == "function"]

        parts: list[str] = [f"# {fp.name}"]
        if classes:
            parts.append(f"\n-- Classes ({len(classes)}) --")
            for lineno, sig in classes:
                parts.append(f"  L{lineno}: {sig}")
        if functions:
            parts.append(f"\n-- Functions ({len(functions)}) --")
            for lineno, sig in functions:
                parts.append(f"  L{lineno}: {sig}")

        if found:
            symbols = [s.split()[0] for _, _, s in found[:20]]
            names = set()
            for s in symbols:
                for token in s.replace("(", " ").split():
                    if token.isidentifier() and not token.startswith(("export", "default", "pub", "async")):
                        names.add(token)
            ref_lines = []
            for name in sorted(names)[:15]:
                refs = self._find_refs(text, name)
                if refs:
                    ref_lines.append(f"  {name}: {', '.join(refs[:3])}")
            if ref_lines:
                parts.append("")
                parts.append("-- cross-references --")
                parts.extend(ref_lines)

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Directory exploration
    # ------------------------------------------------------------------

    def _explore_directory(self, fp: Path, max_level: int) -> str:
        parts: list[str] = [f"# {fp.name}/"]

        by_type: dict[str, list[tuple[str, int]]] = {}
        dirs: list[str] = []
        total_size = 0
        file_count = 0

        try:
            self._walk_dir(fp, fp, by_type, dirs, max_level, 0)
        except PermissionError as e:
            parts.append(f"(Permission denied: {e})")

        for d in sorted(dirs):
            parts.append(f"  {d}/")
        parts.append("")

        for ext in sorted(by_type, key=lambda e: -len(by_type[e])):
            files = sorted(by_type[ext], key=lambda x: -x[1])
            size_info = ""
            if files:
                total_bytes = sum(s for _, s in files)
                size_info = f"  ({self._format_size(total_bytes)})"
                total_size += total_bytes
                file_count += len(files)
            parts.append(f"[{ext or '(no ext)'}]{size_info}")
            for rel, sz in files[:20]:
                parts.append(f"  {rel}  ({self._format_size(sz)})")
            if len(files) > 20:
                parts.append(f"  ... and {len(files) - 20} more")

        parts.append("")
        parts.append(f"({file_count} files, {self._format_size(total_size)} total)")

        return "\n".join(parts)

    def _walk_dir(
        self,
        root: Path,
        current: Path,
        by_type: dict[str, list[tuple[str, int]]],
        dirs: list[str],
        max_level: int,
        level: int,
    ) -> None:
        if level > max_level:
            return
        try:
            entries = sorted(os.scandir(current), key=lambda e: (not e.is_dir(), e.name))
        except PermissionError:
            return

        for entry in entries:
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                if entry.name in _IGNORE_DIRS:
                    continue
                if level < max_level:
                    dirs.append(Path(entry.path).resolve().as_posix())
                self._walk_dir(root, Path(entry.path), by_type, dirs, max_level, level + 1)
            elif entry.is_file():
                ext = Path(entry.name).suffix.lower() or "(no ext)"
                rel = Path(entry.path).resolve().as_posix()
                try:
                    sz = entry.stat().st_size
                except OSError:
                    sz = 0
                by_type.setdefault(ext, []).append((rel, sz))

    @staticmethod
    def _format_size(size: int) -> str:
        if size > 1_000_000:
            return f"{size / 1_000_000:.1f} MB"
        if size > 1_000:
            return f"{size / 1_000:.0f} KB"
        return f"{size} B"