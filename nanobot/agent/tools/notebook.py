"""NotebookEditTool — edit Jupyter .ipynb notebooks."""

from __future__ import annotations

import json
import uuid
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema
from nanobot.agent.tools.filesystem.filesystem import _FsTool


def _new_cell(source: str, cell_type: str = "code", generate_id: bool = False) -> dict:
    cell: dict[str, Any] = {
        "cell_type": cell_type,
        "source": source,
        "metadata": {},
    }
    if cell_type == "code":
        cell["outputs"] = []
        cell["execution_count"] = None
    if generate_id:
        cell["id"] = uuid.uuid4().hex[:8]
    return cell


def _make_empty_notebook() -> dict:
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
        },
        "cells": [],
    }


@tool_parameters(
    build_parameters_schema(
        path=p("string", "Absolute path to a .ipynb notebook file."),
        cell_index=p("integer", "0-based index of the cell to edit (default 0)", minimum=0, default=0),
        new_source=p("string", "New source content for the cell. Required for replace/insert modes; ignored when edit_mode is 'delete'."),
        cell_type=p("string",
            "Cell type: 'code' or 'markdown' (default: code)",
            enum=["code", "markdown"], default="code",
        ),
        edit_mode=p("string",
            "Mode: 'replace' overwrites cell at cell_index, 'insert' adds a new cell after cell_index, 'delete' removes cell at cell_index",
            enum=["replace", "insert", "delete"], default="replace",
        ),
        required=["path", "cell_index"],
    )
)
class NotebookEditTool(_FsTool):
    """Edit Jupyter notebook cells: replace, insert, or delete."""

    _VALID_CELL_TYPES = frozenset({"code", "markdown"})
    _VALID_EDIT_MODES = frozenset({"replace", "insert", "delete"})

    name = "notebook_edit"

    description = (
        "**用途**: 编辑 Jupyter notebook (.ipynb) 单元格。\n\n"
        "**什么时候用**:\n"
        "- 需要自动化编辑 notebook 单元格（替换、插入、删除）\n"
        "- 需要创建新的 notebook 文件\n\n"
        "**什么时候不用**:\n"
        "- 编辑普通文本文件（.py, .md 等）→ 用 edit_file\n"
        "- 只是查看 notebook 内容 → 用 read_file\n"
    )

    async def execute(
        self,
        path: str | None = None,
        cell_index: int = 0,
        new_source: str = "",
        cell_type: str = "code",
        edit_mode: str = "replace",
        **kwargs: Any,
    ) -> str:
        try:
            if not path:
                return "Error: path is required"

            if not path.endswith(".ipynb"):
                return "Error: notebook_edit only works on .ipynb files. Use edit_file for other files."

            if edit_mode not in self._VALID_EDIT_MODES:
                return (
                    f"Error: Invalid edit_mode '{edit_mode}'. "
                    "Use one of: replace, insert, delete."
                )

            if cell_type not in self._VALID_CELL_TYPES:
                return (
                    f"Error: Invalid cell_type '{cell_type}'. "
                    "Use one of: code, markdown."
                )

            fp = self._resolve(path)

            # Create new notebook if file doesn't exist and mode is insert
            if not fp.exists():
                if edit_mode != "insert":
                    return f"Error: File not found: {path}"
                nb = _make_empty_notebook()
                cell = _new_cell(new_source, cell_type, generate_id=True)
                nb["cells"].append(cell)
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_text(json.dumps(nb, indent=1, ensure_ascii=False), encoding="utf-8")
                return f"Successfully created {fp.as_posix()} with 1 cell"

            try:
                nb = json.loads(fp.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                return f"Error: Failed to parse notebook: {e}"

            cells = nb.get("cells", [])
            nbformat_minor = nb.get("nbformat_minor", 0)
            generate_id = nb.get("nbformat", 0) >= 4 and nbformat_minor >= 5

            if edit_mode == "delete":
                if cell_index < 0 or cell_index >= len(cells):
                    return f"Error: cell_index {cell_index} out of range (notebook has {len(cells)} cells)"
                cells.pop(cell_index)
                nb["cells"] = cells
                fp.write_text(json.dumps(nb, indent=1, ensure_ascii=False), encoding="utf-8")
                return f"Successfully deleted cell {cell_index} from {fp.as_posix()}"

            if edit_mode == "insert":
                insert_at = min(cell_index + 1, len(cells))
                cell = _new_cell(new_source, cell_type, generate_id=generate_id)
                cells.insert(insert_at, cell)
                nb["cells"] = cells
                fp.write_text(json.dumps(nb, indent=1, ensure_ascii=False), encoding="utf-8")
                return f"Successfully inserted cell at index {insert_at} in {fp.as_posix()}"

            # Default: replace
            if cell_index < 0 or cell_index >= len(cells):
                return f"Error: cell_index {cell_index} out of range (notebook has {len(cells)} cells)"
            cells[cell_index]["source"] = new_source
            if cell_type and cells[cell_index].get("cell_type") != cell_type:
                cells[cell_index]["cell_type"] = cell_type
                if cell_type == "code":
                    cells[cell_index].setdefault("outputs", [])
                    cells[cell_index].setdefault("execution_count", None)
                elif "outputs" in cells[cell_index]:
                    del cells[cell_index]["outputs"]
                    cells[cell_index].pop("execution_count", None)
            nb["cells"] = cells
            fp.write_text(json.dumps(nb, indent=1, ensure_ascii=False), encoding="utf-8")
            return f"Successfully edited cell {cell_index} in {fp.as_posix()}"

        except PermissionError as e:
            logger.warning("NotebookEdit permission denied: {}", e)
            return f"Error: {e}"
        except Exception as e:
            logger.warning("NotebookEdit failed: {}", e)
            return f"Error editing notebook: {e}"
