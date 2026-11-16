"""Validated, allowlisted execution of registered business tools."""
from __future__ import annotations

from typing import Any

from src.agent.result_formatter import (
    ResultSerializationError,
    format_failure,
    format_success,
)
from src.agent.tool_registry import ToolNotFoundError, ToolRegistry
from src.agent.tool_schemas import SchemaValidationError


class ToolExecutor:
    """Run only callables already held by an injected ToolRegistry."""

    def __init__(self, registry: ToolRegistry) -> None:
        if not isinstance(registry, ToolRegistry):
            raise TypeError("registry must be a ToolRegistry instance.")
        self.registry = registry

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            definition = self.registry.get_tool(tool_name)
        except ToolNotFoundError as exc:
            return format_failure(str(tool_name), "tool_not_found", str(exc))

        try:
            validated = definition.schema.validate(arguments)
        except SchemaValidationError as exc:
            return format_failure(
                definition.name,
                "argument_validation_error",
                str(exc),
                metadata={"read_only": definition.read_only},
            )

        try:
            result = definition.execute(**validated)
        except Exception as exc:
            return format_failure(
                definition.name,
                "tool_execution_error",
                str(exc) or exc.__class__.__name__,
                details={"exception_type": exc.__class__.__name__},
                metadata={"read_only": definition.read_only},
            )

        if isinstance(result, dict) and result.get("ok") is False:
            try:
                return format_failure(
                    definition.name,
                    "business_tool_failed",
                    "The business tool returned an explicit failure result.",
                    details={"business_result": result},
                    metadata={"read_only": definition.read_only},
                )
            except ResultSerializationError as exc:
                return format_failure(
                    definition.name,
                    "result_serialization_error",
                    str(exc),
                    metadata={"read_only": definition.read_only},
                )
        try:
            return format_success(
                definition.name,
                result,
                metadata={"read_only": definition.read_only},
            )
        except ResultSerializationError as exc:
            return format_failure(
                definition.name,
                "result_serialization_error",
                str(exc),
                metadata={"read_only": definition.read_only},
            )
