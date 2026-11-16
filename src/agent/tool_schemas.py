"""JSON-Schema-style definitions and validation for registered business tools.

Only Agent-facing business parameters are exposed here.  Database paths and
query-runner objects remain internal dependency-injection details, so a future
Agent cannot use this layer to select an arbitrary database or callable.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date
import re
from types import MappingProxyType
from typing import Any, Mapping


class SchemaValidationError(ValueError):
    """Raised when tool arguments do not satisfy an Agent-facing schema."""


_MISSING = object()
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class ParameterDefinition:
    """Immutable definition of one callable business parameter."""

    name: str
    json_type: str
    required: bool
    description: str
    default: Any = _MISSING
    nullable: bool = False
    enum: tuple[Any, ...] = ()
    item_type: str | None = None
    minimum: int | None = None
    maximum: int | None = None
    min_items: int | None = None
    unique_items: bool = False
    format: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-Schema-style, JSON-serializable representation."""
        type_value: str | list[str]
        type_value = [self.json_type, "null"] if self.nullable else self.json_type
        result: dict[str, Any] = {
            "type": type_value,
            "description": self.description,
        }
        if self.default is not _MISSING:
            default = deepcopy(self.default)
            result["default"] = list(default) if isinstance(default, tuple) else default
        if self.enum:
            result["enum"] = list(self.enum) + ([None] if self.nullable else [])
        if self.item_type is not None:
            result["items"] = {"type": self.item_type}
        if self.minimum is not None:
            result["minimum"] = self.minimum
        if self.maximum is not None:
            result["maximum"] = self.maximum
        if self.min_items is not None:
            result["minItems"] = self.min_items
        if self.unique_items:
            result["uniqueItems"] = True
        if self.format is not None:
            result["format"] = self.format
        return result


@dataclass(frozen=True)
class ToolSchema:
    """Immutable ordered schema with standard-library argument validation."""

    parameters: tuple[ParameterDefinition, ...]
    additional_properties: bool = False

    def __post_init__(self) -> None:
        names = [parameter.name for parameter in self.parameters]
        if len(names) != len(set(names)):
            raise ValueError("Tool schema parameter names must be unique.")

    def to_dict(self) -> dict[str, Any]:
        required = [p.name for p in self.parameters if p.required]
        return {
            "type": "object",
            "properties": {p.name: p.to_dict() for p in self.parameters},
            "required": required,
            "additionalProperties": self.additional_properties,
        }

    def validate(self, arguments: object) -> dict[str, Any]:
        """Validate arguments and return a new mapping with safe defaults."""
        if not isinstance(arguments, dict):
            raise SchemaValidationError("arguments must be a dictionary.")
        definitions = {p.name: p for p in self.parameters}
        extra = sorted(
            (key for key in arguments if key not in definitions), key=str
        )
        if extra and not self.additional_properties:
            raise SchemaValidationError(
                "Unexpected argument(s): " + ", ".join(str(item) for item in extra)
            )

        missing = [p.name for p in self.parameters if p.required and p.name not in arguments]
        if missing:
            raise SchemaValidationError(
                "Missing required argument(s): " + ", ".join(missing)
            )

        validated: dict[str, Any] = {}
        for parameter in self.parameters:
            if parameter.name in arguments:
                value = arguments[parameter.name]
            elif parameter.default is not _MISSING:
                value = deepcopy(parameter.default)
                if parameter.json_type == "array" and isinstance(value, tuple):
                    value = list(value)
            else:
                continue
            _validate_value(parameter, value)
            validated[parameter.name] = value
        return validated


def _validate_value(parameter: ParameterDefinition, value: Any) -> None:
    if value is None:
        if parameter.nullable:
            return
        raise SchemaValidationError(f"{parameter.name} cannot be null.")

    if parameter.json_type == "string":
        valid_type = isinstance(value, str)
    elif parameter.json_type == "integer":
        valid_type = isinstance(value, int) and not isinstance(value, bool)
    elif parameter.json_type == "array":
        valid_type = isinstance(value, list)
    elif parameter.json_type == "boolean":
        valid_type = isinstance(value, bool)
    else:  # A programming error in a schema must never silently accept input.
        raise RuntimeError(f"Unsupported schema type: {parameter.json_type}")
    if not valid_type:
        raise SchemaValidationError(
            f"{parameter.name} must have JSON type {parameter.json_type}."
        )

    if parameter.enum and value not in parameter.enum:
        allowed = ", ".join(repr(item) for item in parameter.enum)
        raise SchemaValidationError(f"{parameter.name} must be one of: {allowed}.")
    if parameter.format == "date":
        if not _DATE_PATTERN.fullmatch(value):
            raise SchemaValidationError(f"{parameter.name} must use YYYY-MM-DD format.")
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise SchemaValidationError(
                f"{parameter.name} must be a valid calendar date."
            ) from exc
    if parameter.json_type == "integer":
        if parameter.minimum is not None and value < parameter.minimum:
            raise SchemaValidationError(
                f"{parameter.name} must be at least {parameter.minimum}."
            )
        if parameter.maximum is not None and value > parameter.maximum:
            raise SchemaValidationError(
                f"{parameter.name} must be at most {parameter.maximum}."
            )
    if parameter.json_type == "array":
        if parameter.min_items is not None and len(value) < parameter.min_items:
            raise SchemaValidationError(
                f"{parameter.name} must contain at least {parameter.min_items} item(s)."
            )
        if parameter.item_type == "integer":
            for item in value:
                if isinstance(item, bool) or not isinstance(item, int):
                    raise SchemaValidationError(
                        f"Every item in {parameter.name} must be an integer."
                    )
                if parameter.minimum is not None and item < parameter.minimum:
                    raise SchemaValidationError(
                        f"Every item in {parameter.name} must be at least {parameter.minimum}."
                    )
        if parameter.unique_items:
            try:
                has_duplicates = len(value) != len(set(value))
            except TypeError as exc:
                raise SchemaValidationError(
                    f"Items in {parameter.name} must be scalar values."
                ) from exc
            if has_duplicates:
                raise SchemaValidationError(f"{parameter.name} cannot contain duplicates.")


def _date_parameter(name: str, description: str, *, required: bool) -> ParameterDefinition:
    return ParameterDefinition(
        name=name,
        json_type="string",
        required=required,
        description=description,
        default=_MISSING if required else None,
        nullable=not required,
        format="date",
    )


ACTIVATION_SCHEMA = ToolSchema((
    _date_parameter("cohort_start", "注册 cohort 起始 UTC 日期（含当天）。", required=True),
    _date_parameter("cohort_end", "注册 cohort 结束 UTC 日期（含当天）。", required=True),
    ParameterDefinition("observation_days", "integer", False, "注册后的观察窗口天数。", 7, minimum=1),
    _date_parameter("dataset_end_date", "可靠数据覆盖截止 UTC 日期；缺省读取配置。", required=False),
))

RETENTION_SCHEMA = ToolSchema((
    _date_parameter("cohort_start", "注册 cohort 起始 UTC 日期（含当天）。", required=True),
    _date_parameter("cohort_end", "注册 cohort 结束 UTC 日期（含当天）。", required=True),
    ParameterDefinition(
        "retention_days", "array", False, "精确日期留存节点，例如 [1, 7, 30]。",
        (1, 7, 30), item_type="integer", minimum=1, min_items=1, unique_items=True,
    ),
    _date_parameter("data_through_date", "可靠数据覆盖截止 UTC 日期；缺省读取配置。", required=False),
    ParameterDefinition(
        "breakdown_by", "string", False, "可选分组字段。", None, nullable=True,
        enum=("acquisition_channel", "plan_type"),
    ),
))

ACTIVITY_SCHEMA = ToolSchema((
    _date_parameter("start_date", "活跃趋势起始 UTC 日期（含当天）。", required=True),
    _date_parameter("end_date", "活跃趋势结束 UTC 日期（含当天）。", required=True),
    _date_parameter("data_start_date", "可靠数据覆盖起始 UTC 日期；缺省读取配置。", required=False),
    _date_parameter("data_through_date", "可靠数据覆盖截止 UTC 日期；缺省读取配置。", required=False),
    _date_parameter("as_of_date", "DAU/WAU/MAU 基准 UTC 日期；缺省为 end_date。", required=False),
    ParameterDefinition(
        "breakdown_by", "string", False, "可选分组字段。", None, nullable=True,
        enum=("acquisition_channel", "plan_type"),
    ),
))

FEEDBACK_SCHEMA = ToolSchema((
    _date_parameter("start_date", "反馈统计起始 UTC 日期（含当天）。", required=True),
    _date_parameter("end_date", "反馈统计结束 UTC 日期（含当天）。", required=True),
    ParameterDefinition(
        "breakdown_by", "string", False, "可选分组字段。", None, nullable=True,
        enum=("channel", "acquisition_channel", "plan_type"),
    ),
    ParameterDefinition(
        "analysis_mode", "string", False, "rules 执行规则分类；none 仅统计原始数据。",
        "rules", enum=("rules", "none"),
    ),
    _date_parameter("data_through_date", "可靠数据覆盖截止 UTC 日期；缺省读取配置。", required=False),
    ParameterDefinition("page_size", "integer", False, "稳定分页的每页记录数。", 100, minimum=1, maximum=200),
    ParameterDefinition("top_n", "integer", False, "返回的高频原始文本数量。", 10, minimum=1),
    _date_parameter("data_start_date", "可靠数据覆盖起始 UTC 日期；缺省读取配置。", required=False),
))


TOOL_SCHEMAS: Mapping[str, ToolSchema] = MappingProxyType({
    "analyze_activation_funnel": ACTIVATION_SCHEMA,
    "analyze_retention": RETENTION_SCHEMA,
    "analyze_user_activity": ACTIVITY_SCHEMA,
    "analyze_feedback_trends": FEEDBACK_SCHEMA,
})


def get_tool_schema(tool_name: str) -> ToolSchema:
    """Return the immutable schema for a built-in business tool."""
    try:
        return TOOL_SCHEMAS[tool_name]
    except KeyError as exc:
        raise KeyError(f"No schema is defined for tool: {tool_name}") from exc
