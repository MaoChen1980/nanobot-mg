"""JSON Schema property builder and validation.

Usage::

    from nanobot.agent.tools.schema import p

    @tool_parameters(properties={
        "path": p("string", "The file path"),
        "offset": p("integer", "Line number", minimum=1),
    }, required=["path"])
    class MyTool(Tool):
        ...
"""

from __future__ import annotations

from typing import Any



def p(type: str, description: str = "", **kwargs: Any) -> dict[str, Any]:
    """Build a JSON Schema property dict.

    Args:
        type: JSON Schema type (e.g. ``"string"``, ``"integer"``, ``"boolean"``).
        description: Human-readable description for the LLM.
        **kwargs: Additional JSON Schema keywords (e.g. ``minimum=``, ``enum=``).

    Returns a plain dict suitable for use in ``@tool_parameters(properties=...)``.
    """
    d: dict[str, Any] = {"type": type}
    if description:
        d["description"] = description
    else:
        # Always provide a non-empty description so LLM knows what this param is
        d["description"] = f"Parameter of type {type}"
    d.update(kwargs)
    return d


def build_parameters_schema(
    *,
    required: list[str] | None = None,
    description: str = "",
    **properties: Any,
) -> dict[str, Any]:
    """Build a full ``{"type": "object", "properties": ...}`` dict.

    Convenience wrapper around :func:`p`.  You may also write plain dicts::

        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "..."},
            },
            "required": ["path"],
        }

    Note:
        *description* sets the **root-level** description, not per-property.
    """
    out: dict[str, Any] = {"type": "object", "properties": {}}
    for key, value in properties.items():
        out["properties"][key] = value
    if required:
        out["required"] = required
    if description:
        out["description"] = description
    return out
