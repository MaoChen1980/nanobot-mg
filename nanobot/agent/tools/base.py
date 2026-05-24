"""Base class for agent tools and JSON Schema validation helpers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any, TypeVar

_ToolT = TypeVar("_ToolT", bound="Tool")

_JSON_TYPE_MAP: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


class Schema(ABC):
    """Abstract base for JSON Schema fragments describing tool parameters."""

    @staticmethod
    def resolve_json_schema_type(t: Any) -> str | None:
        """Resolve the non-null type name from JSON Schema ``type`` (e.g. ``['string','null']`` -> ``'string'``)."""
        if isinstance(t, list):
            return next((x for x in t if x != "null"), None)
        return t  # type: ignore[return-value]

    @staticmethod
    def subpath(path: str, key: str) -> str:
        return f"{path}.{key}" if path else key

    @staticmethod
    def validate_json_schema_value(val: Any, schema: dict[str, Any], path: str = "") -> list[str]:
        """Validate ``val`` against a JSON Schema fragment; returns error messages (empty means valid)."""
        raw_type = schema.get("type")
        nullable = (isinstance(raw_type, list) and "null" in raw_type) or schema.get("nullable", False)
        t = Schema.resolve_json_schema_type(raw_type)
        label = path or "parameter"

        if nullable and val is None:
            return []
        if t == "integer" and (not isinstance(val, int) or isinstance(val, bool)):
            return [f"{label} should be integer"]
        if t == "number" and (
            not isinstance(val, _JSON_TYPE_MAP["number"]) or isinstance(val, bool)
        ):
            return [f"{label} should be number"]
        if t in _JSON_TYPE_MAP and t not in ("integer", "number") and not isinstance(val, _JSON_TYPE_MAP[t]):
            return [f"{label} should be {t}"]

        errors: list[str] = []
        if "enum" in schema and val not in schema["enum"]:
            errors.append(f"{label} must be one of {schema['enum']}")
        if t in ("integer", "number"):
            if "minimum" in schema and val < schema["minimum"]:
                errors.append(f"{label} must be >= {schema['minimum']}")
            if "maximum" in schema and val > schema["maximum"]:
                errors.append(f"{label} must be <= {schema['maximum']}")
        if t == "string":
            if "minLength" in schema and len(val) < schema["minLength"]:
                errors.append(f"{label} must be at least {schema['minLength']} chars")
            if "maxLength" in schema and len(val) > schema["maxLength"]:
                errors.append(f"{label} must be at most {schema['maxLength']} chars")
        if t == "object":
            props = schema.get("properties", {})
            for k in schema.get("required", []):
                if k not in val:
                    errors.append(f"missing required {Schema.subpath(path, k)}")
            for k, v in val.items():
                if k in props:
                    errors.extend(Schema.validate_json_schema_value(v, props[k], Schema.subpath(path, k)))
        if t == "array":
            if "minItems" in schema and len(val) < schema["minItems"]:
                errors.append(f"{label} must have at least {schema['minItems']} items")
            if "maxItems" in schema and len(val) > schema["maxItems"]:
                errors.append(f"{label} must be at most {schema['maxItems']} items")
            if "items" in schema:
                prefix = f"{path}[{{}}]" if path else "[{}]"
                for i, item in enumerate(val):
                    errors.extend(
                        Schema.validate_json_schema_value(item, schema["items"], prefix.format(i))
                    )
        return errors

    @staticmethod
    def fragment(value: Any) -> dict[str, Any]:
        """Normalize a Schema instance or raw dict to a JSON Schema fragment."""
        to_js = getattr(value, "to_json_schema", None)
        if callable(to_js):
            return to_js()
        if isinstance(value, dict):
            return value
        raise TypeError(f"Expected schema object or dict, got {type(value).__name__}")

    @abstractmethod
    def to_json_schema(self) -> dict[str, Any]:
        ...

    def validate_value(self, value: Any, path: str = "") -> list[str]:
        return Schema.validate_json_schema_value(value, self.to_json_schema(), path)


# ---------------------------------------------------------------------------
# Validator framework
# ---------------------------------------------------------------------------


class Validator(ABC):
    """Base class for tool validators.

    A validator runs before (pre) or after (post) tool execution.
    Return ``None`` for pass, or an error/warning string on failure.
    """

    @abstractmethod
    async def check(self, tool: Tool, params: dict[str, Any], result: Any = None) -> str | None:
        ...

    @staticmethod
    def _resolve(tool: Tool, val: str) -> Path:
        """Resolve a path using the tool's ``_resolve`` if available."""
        resolve = getattr(tool, "_resolve", None)
        if resolve:
            return resolve(val)
        return Path(val)


class PathExists(Validator):
    """Pre-validator: parameter must point to an existing path."""

    def __init__(self, key: str) -> None:
        self._key = key

    async def check(self, tool: Tool, params: dict[str, Any], result: Any = None) -> str | None:
        val = params.get(self._key)
        if not val:
            return None
        try:
            resolved = self._resolve(tool, val)
        except Exception as e:
            return f"{self._key} cannot be resolved: {e}"
        if not resolved.exists():
            return f"{self._key} does not exist: {resolved.as_posix()}"
        return None


class PathNotExists(Validator):
    """Pre-validator: parameter must not already exist."""

    def __init__(self, key: str) -> None:
        self._key = key

    async def check(self, tool: Tool, params: dict[str, Any], result: Any = None) -> str | None:
        val = params.get(self._key)
        if not val:
            return None
        try:
            resolved = self._resolve(tool, val)
        except Exception as e:
            return f"{self._key} cannot be resolved: {e}"
        if resolved.exists():
            return f"{self._key} already exists: {resolved.as_posix()}"
        return None


class PathType(Validator):
    """Pre-validator: parameter must be a file or directory."""

    def __init__(self, key: str, kind: str) -> None:
        self._key = key
        self._kind = kind  # "file" or "dir"

    async def check(self, tool: Tool, params: dict[str, Any], result: Any = None) -> str | None:
        val = params.get(self._key)
        if not val:
            return None
        try:
            resolved = self._resolve(tool, val)
        except Exception as e:
            return f"{self._key} cannot be resolved: {e}"
        if not resolved.exists():
            return None  # PathExists should catch this first
        if self._kind == "file" and not resolved.is_file():
            return f"{self._key} is a directory, expected a file: {resolved.as_posix()}"
        if self._kind == "dir" and not resolved.is_dir():
            return f"{self._key} is a file, expected a directory: {resolved.as_posix()}"
        return None


class FileDeleted(Validator):
    """Post-validator: confirm a file was deleted."""

    def __init__(self, key: str) -> None:
        self._key = key

    async def check(self, tool: Tool, params: dict[str, Any], result: Any = None) -> str | None:
        val = params.get(self._key)
        if not val:
            return None
        try:
            resolved = self._resolve(tool, val)
        except Exception:
            return None
        if resolved.exists():
            return f"{self._key} still exists after delete: {resolved.as_posix()}"
        return None


class FileCreated(Validator):
    """Post-validator: confirm a file was created."""

    def __init__(self, key: str) -> None:
        self._key = key

    async def check(self, tool: Tool, params: dict[str, Any], result: Any = None) -> str | None:
        val = params.get(self._key)
        if not val:
            return None
        try:
            resolved = self._resolve(tool, val)
        except Exception:
            return None
        if not resolved.exists():
            return f"{self._key} not found after operation: {resolved.as_posix()}"
        return None


class ExitCode(Validator):
    """Post-validator: check command exit code matches expected."""

    def __init__(self, expected: int = 0) -> None:
        self._expected = expected

    async def check(self, tool: Tool, params: dict[str, Any], result: Any = None) -> str | None:
        if result is None:
            return "no result to check"
        if isinstance(result, str) and "Exit code:" in result:
            import re
            m = re.search(r"Exit code:\s*(-?\d+)", result)
            if m:
                code = int(m.group(1))
                if code != self._expected:
                    return f"exit code {code} ≠ expected {self._expected}"
        return None


# ---------------------------------------------------------------------------
# Tool base class
# ---------------------------------------------------------------------------


class Tool(ABC):
    """Agent capability: read files, run commands, etc.

    Subclasses set :attr:`name`, :attr:`description`, :attr:`read_only`,
    and :attr:`exclusive` as class attributes.  The :attr:`parameters` schema
    is attached via the :func:`tool_parameters` decorator.

    Subclasses can declare :attr:`_pre_validators` and :attr:`_post_validators`
    to have the framework automatically verify conditions before and after
    :meth:`execute` runs.
    """

    name: str = ""
    description: str = ""
    read_only: bool = False
    exclusive: bool = False

    _tool_parameters_schema: dict[str, Any] = {"type": "object", "properties": {}}
    """Cached JSON Schema (set by :func:`tool_parameters`)."""

    _pre_validators: list[Validator] = []
    """Validators run before :meth:`execute`. Return error string to abort."""

    _post_validators: list[Validator] = []
    """Validators run after :meth:`execute`. Return warning string on failure."""

    @property
    def concurrency_safe(self) -> bool:
        return self.read_only and not self.exclusive

    @property
    def parameters(self) -> dict[str, Any]:
        """JSON Schema for tool parameters (cached, read-only)."""
        return self._tool_parameters_schema

    _TYPE_MAP = _JSON_TYPE_MAP
    _BOOL_TRUE = frozenset(("true", "1", "yes"))
    _BOOL_FALSE = frozenset(("false", "0", "no"))

    @staticmethod
    def _resolve_type(t: Any) -> str | None:
        return Schema.resolve_json_schema_type(t)

    def _cast_object(self, obj: Any, schema: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(obj, dict):
            return obj
        props = schema.get("properties", {})
        return {k: self._cast_value(v, props[k]) if k in props else v for k, v in obj.items()}

    def cast_params(self, params: dict[str, Any]) -> dict[str, Any]:
        schema = self.parameters
        if schema.get("type", "object") != "object":
            return params
        return self._cast_object(params, schema)

    def _cast_value(self, val: Any, schema: dict[str, Any]) -> Any:
        t = self._resolve_type(schema.get("type"))
        if t == "boolean" and isinstance(val, bool):
            return val
        if t == "boolean" and isinstance(val, int) and val in (0, 1):
            return bool(val)
        if t == "number" and isinstance(val, int) and not isinstance(val, bool):
            return float(val)
        if t == "integer" and isinstance(val, int) and not isinstance(val, bool):
            return val
        if t in self._TYPE_MAP and t not in ("boolean", "integer", "array", "object"):
            expected = self._TYPE_MAP[t]
            if isinstance(val, expected):
                return val
        if isinstance(val, str) and t in ("integer", "number"):
            try:
                return int(val) if t == "integer" else float(val)
            except ValueError:
                return val
        if t == "string":
            return val if val is None else str(val)
        if t == "boolean" and isinstance(val, str):
            low = val.lower()
            if low in self._BOOL_TRUE:
                return True
            if low in self._BOOL_FALSE:
                return False
            return val
        if t == "array" and isinstance(val, list):
            items = schema.get("items")
            return [self._cast_value(x, items) for x in val] if items else val
        if t == "object" and isinstance(val, dict):
            return self._cast_object(val, schema)
        return val

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        if not isinstance(params, dict):
            return [f"parameters must be an object, got {type(params).__name__}"]
        schema = self.parameters
        errors = Schema.validate_json_schema_value(params, {**schema, "type": "object"}, "")
        # Enrich errors with parameter context so LLM knows which param failed
        enriched = []
        for msg in errors:
            # Try to extract the parameter name from the path prefix (e.g. "foo.bar" -> "foo.bar")
            enriched.append(msg)
        return enriched

    def to_schema(self) -> dict[str, Any]:
        """OpenAI function schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    @abstractmethod
    async def execute(self, **kwargs: Any) -> Any:
        ...


def tool_parameters(
    schema: dict[str, Any] | None = None,
    *,
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
    description: str = "",
) -> Callable[[type[_ToolT]], type[_ToolT]]:
    """Class decorator that attaches a JSON Schema to a :class:`Tool`.

    New style (recommended)::

        @tool_parameters(properties={
            "path": p("string", "The file path"),
        }, required=["path"])
        class MyTool(Tool): ...

    Legacy style (full schema dict)::

        @tool_parameters({"type": "object", "properties": {...}, "required": ["path"]})
        class MyTool(Tool): ...
    """
    if schema is None and properties is not None:
        schema = {"type": "object", "properties": dict(properties)}
        if required:
            schema["required"] = list(required)
        if description:
            schema["description"] = description
    elif schema is None:
        schema = {"type": "object", "properties": {}}

    def decorator(cls: type[_ToolT]) -> type[_ToolT]:
        schema_copy = deepcopy(schema)  # type: ignore[assignment]
        # Auto-inject minLength: 1 for required string params
        required = schema_copy.get("required", [])
        props = schema_copy.get("properties", {})
        for key in required:
            prop = props.get(key, {})
            if isinstance(prop, dict) and prop.get("type") == "string" and "minLength" not in prop:
                prop["minLength"] = 1
        cls._tool_parameters_schema = schema_copy
        return cls

    return decorator
