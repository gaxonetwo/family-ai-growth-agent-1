"""Tests for the explicit business-tool allowlist."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
import unittest

from src.agent.tool_registry import (
    ToolAlreadyRegisteredError,
    ToolDefinition,
    ToolNotFoundError,
    ToolRegistry,
    ToolRegistryError,
    create_default_tool_registry,
    register_business_tools,
)
from src.agent.tool_schemas import ACTIVATION_SCHEMA


def _noop(**arguments):
    return arguments


def _definition(name: str = "sample") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="测试描述",
        purpose="测试用途",
        schema=ACTIVATION_SCHEMA,
        execute=_noop,
        read_only=True,
    )


class ToolRegistryTests(unittest.TestCase):
    def test_register_and_get_existing_tool(self):
        registry = ToolRegistry()
        definition = _definition()
        registry.register_tool(definition)
        self.assertIs(registry.get_tool("sample"), definition)

    def test_duplicate_registration_is_rejected(self):
        registry = ToolRegistry()
        registry.register_tool(_definition())
        with self.assertRaisesRegex(ToolAlreadyRegisteredError, "already registered"):
            registry.register_tool(_definition())

    def test_missing_tool_has_explicit_error(self):
        with self.assertRaisesRegex(ToolNotFoundError, "not registered"):
            ToolRegistry().get_tool("missing")

    def test_list_tools_is_deterministic(self):
        registry = ToolRegistry()
        registry.register_tool(_definition("z_tool"))
        registry.register_tool(_definition("a_tool"))
        self.assertEqual([item.name for item in registry.list_tools()], ["a_tool", "z_tool"])

    def test_unregister_returns_definition_and_removes_it(self):
        registry = ToolRegistry()
        definition = _definition()
        registry.register_tool(definition)
        self.assertIs(registry.unregister_tool("sample"), definition)
        with self.assertRaises(ToolNotFoundError):
            registry.get_tool("sample")

    def test_invalid_registration_values_are_rejected(self):
        with self.assertRaises(ToolRegistryError):
            _definition("")
        with self.assertRaises(ToolRegistryError):
            ToolDefinition("x", "描述", "用途", ACTIVATION_SCHEMA, None, True)  # type: ignore[arg-type]
        with self.assertRaises(ToolRegistryError):
            ToolRegistry().register_tool(object())  # type: ignore[arg-type]

    def test_definition_is_immutable(self):
        definition = _definition()
        with self.assertRaises(FrozenInstanceError):
            definition.name = "changed"  # type: ignore[misc]

    def test_descriptions_include_schema_and_business_metadata(self):
        registry = ToolRegistry()
        registry.register_tool(_definition())
        description = registry.get_tool_descriptions()[0]
        self.assertEqual(description["name"], "sample")
        self.assertTrue(description["read_only"])
        self.assertFalse(description["parameters"]["additionalProperties"])
        self.assertIn("cohort_start", description["parameters"]["required"])

    def test_default_registry_contains_exactly_four_read_only_tools(self):
        registry = create_default_tool_registry()
        self.assertEqual(
            {item.name for item in registry.list_tools()},
            {
                "analyze_activation_funnel",
                "analyze_retention",
                "analyze_user_activity",
                "analyze_feedback_trends",
            },
        )
        self.assertTrue(all(item.read_only for item in registry.list_tools()))

    def test_register_business_tools_repeated_call_is_explicit_error(self):
        registry = create_default_tool_registry()
        with self.assertRaisesRegex(ToolAlreadyRegisteredError, "already registered"):
            register_business_tools(registry)


if __name__ == "__main__":
    unittest.main()
