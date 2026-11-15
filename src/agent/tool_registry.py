"""Registry of explicitly approved Family AI business tools."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from src.agent.tool_schemas import TOOL_SCHEMAS, ToolSchema
from src.analytics.activation import analyze_activation_funnel
from src.analytics.activity import analyze_user_activity
from src.analytics.feedback import analyze_feedback_trends
from src.analytics.retention import analyze_retention


ToolCallable = Callable[..., Any]


class ToolRegistryError(ValueError):
    """Base error for invalid registry operations."""


class ToolAlreadyRegisteredError(ToolRegistryError):
    """Raised when a name is registered more than once."""


class ToolNotFoundError(ToolRegistryError):
    """Raised when a requested tool is not registered."""


@dataclass(frozen=True)
class ToolDefinition:
    """Immutable metadata plus the approved callable for one business tool."""

    name: str
    description: str
    purpose: str
    schema: ToolSchema
    execute: ToolCallable
    read_only: bool

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ToolRegistryError("Tool name must be a non-empty string.")
        if not isinstance(self.description, str) or not self.description.strip():
            raise ToolRegistryError("Tool description must be a non-empty string.")
        if not isinstance(self.purpose, str) or not self.purpose.strip():
            raise ToolRegistryError("Tool purpose must be a non-empty string.")
        if not isinstance(self.schema, ToolSchema):
            raise ToolRegistryError("Tool schema must be a ToolSchema instance.")
        if not callable(self.execute):
            raise ToolRegistryError("Tool execute value must be callable.")
        if not isinstance(self.read_only, bool):
            raise ToolRegistryError("Tool read_only must be a boolean.")


class ToolRegistry:
    """In-memory allowlist; never imports or evaluates a caller-provided name."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register_tool(self, definition: ToolDefinition) -> None:
        if not isinstance(definition, ToolDefinition):
            raise ToolRegistryError("Only ToolDefinition instances can be registered.")
        if definition.name in self._tools:
            raise ToolAlreadyRegisteredError(
                f"Tool is already registered: {definition.name}"
            )
        self._tools[definition.name] = definition

    def get_tool(self, name: str) -> ToolDefinition:
        try:
            return self._tools[name]
        except (KeyError, TypeError) as exc:
            raise ToolNotFoundError(f"Tool is not registered: {name}") from exc

    def list_tools(self) -> tuple[ToolDefinition, ...]:
        """Return definitions in deterministic name order."""
        return tuple(self._tools[name] for name in sorted(self._tools))

    def get_tool_descriptions(self) -> list[dict[str, Any]]:
        """Return JSON-compatible metadata suitable for a future Agent."""
        return [
            {
                "name": item.name,
                "description": item.description,
                "purpose": item.purpose,
                "read_only": item.read_only,
                "parameters": item.schema.to_dict(),
            }
            for item in self.list_tools()
        ]

    def unregister_tool(self, name: str) -> ToolDefinition:
        definition = self.get_tool(name)
        del self._tools[name]
        return definition


_BUSINESS_TOOL_DEFINITIONS = (
    ToolDefinition(
        "analyze_activation_funnel",
        "分析注册用户在完整观察窗口内依次完成设备绑定、新手引导和首次 AI 交互的激活漏斗。",
        "回答注册、各激活阶段转化、流失和整体激活率问题。",
        TOOL_SCHEMAS["analyze_activation_funnel"],
        analyze_activation_funnel,
        True,
    ),
    ToolDefinition(
        "analyze_retention",
        "分析成熟注册 cohort 的精确日期 D1、D7、D30 等留存，并支持渠道或当前套餐分组。",
        "回答用户是否在注册后指定日返回，以及不同群体的留存差异。",
        TOOL_SCHEMAS["analyze_retention"],
        analyze_retention,
        True,
    ),
    ToolDefinition(
        "analyze_user_activity",
        "分析有效使用事件产生的 DAU、滚动 WAU、滚动 MAU 和活跃趋势。",
        "回答活跃用户规模、事件量、时间趋势及渠道或当前套餐差异。",
        TOOL_SCHEMAS["analyze_user_activity"],
        analyze_user_activity,
        True,
    ),
    ToolDefinition(
        "analyze_feedback_trends",
        "统计反馈量、规则主题与情感、负面趋势、高频原文及可选业务分组。",
        "回答用户反馈内容、主题、情感和趋势问题；规则模式不调用大模型。",
        TOOL_SCHEMAS["analyze_feedback_trends"],
        analyze_feedback_trends,
        True,
    ),
)


def register_business_tools(registry: ToolRegistry) -> ToolRegistry:
    """Register all four tools; a repeated call raises a clear duplicate error."""
    if not isinstance(registry, ToolRegistry):
        raise ToolRegistryError("registry must be a ToolRegistry instance.")
    for definition in _BUSINESS_TOOL_DEFINITIONS:
        registry.register_tool(definition)
    return registry


def create_default_tool_registry() -> ToolRegistry:
    """Create a fresh registry containing exactly the four approved tools."""
    return register_business_tools(ToolRegistry())
