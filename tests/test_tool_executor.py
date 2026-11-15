"""Unit and integration tests for validated, registered tool execution."""
from __future__ import annotations

import inspect
from unittest import mock
import unittest

from src.analytics.activation import analyze_activation_funnel
from src.analytics.activity import analyze_user_activity
from src.analytics.feedback import analyze_feedback_trends
from src.analytics.retention import analyze_retention
from src.agent.tool_executor import ToolExecutor
from src.agent.tool_registry import ToolDefinition, ToolRegistry, create_default_tool_registry
from src.agent.tool_schemas import (
    ACTIVATION_SCHEMA,
    ACTIVITY_SCHEMA,
    FEEDBACK_SCHEMA,
    RETENTION_SCHEMA,
    ParameterDefinition,
    SchemaValidationError,
    ToolSchema,
)


SIMPLE_SCHEMA = ToolSchema((
    ParameterDefinition("value", "integer", True, "测试整数。", minimum=1),
    ParameterDefinition("mode", "string", False, "测试模式。", "safe", enum=("safe", "fast")),
))


def _registry_with(function, *, name="sample", schema=SIMPLE_SCHEMA):
    registry = ToolRegistry()
    registry.register_tool(ToolDefinition(
        name=name,
        description="测试工具",
        purpose="验证执行器",
        schema=schema,
        execute=function,
        read_only=True,
    ))
    return registry


class ToolExecutorTests(unittest.TestCase):
    def test_normal_execution_applies_default_and_calls_once(self):
        function = mock.Mock(return_value={"ok": True, "answer": 2})
        result = ToolExecutor(_registry_with(function)).execute("sample", {"value": 2})
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["answer"], 2)
        function.assert_called_once_with(value=2, mode="safe")

    def test_unknown_tool_cannot_execute_unregistered_name(self):
        function = mock.Mock()
        executor = ToolExecutor(_registry_with(function))
        result = executor.execute("os.system", {"value": 1})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "tool_not_found")
        function.assert_not_called()

    def test_missing_required_argument_does_not_call_tool(self):
        function = mock.Mock()
        result = ToolExecutor(_registry_with(function)).execute("sample", {})
        self.assertEqual(result["error"]["code"], "argument_validation_error")
        function.assert_not_called()

    def test_arguments_must_be_dictionary(self):
        function = mock.Mock()
        result = ToolExecutor(_registry_with(function)).execute("sample", [1])  # type: ignore[arg-type]
        self.assertEqual(result["error"]["code"], "argument_validation_error")
        function.assert_not_called()

    def test_extra_argument_does_not_call_tool(self):
        function = mock.Mock()
        result = ToolExecutor(_registry_with(function)).execute(
            "sample", {"value": 1, "unexpected": True}
        )
        self.assertIn("Unexpected", result["error"]["message"])
        function.assert_not_called()

    def test_wrong_type_and_bool_as_integer_are_rejected(self):
        for bad_value in ("1", True):
            with self.subTest(value=bad_value):
                function = mock.Mock()
                result = ToolExecutor(_registry_with(function)).execute(
                    "sample", {"value": bad_value}
                )
                self.assertFalse(result["ok"])
                function.assert_not_called()

    def test_invalid_enum_does_not_call_tool(self):
        function = mock.Mock()
        result = ToolExecutor(_registry_with(function)).execute(
            "sample", {"value": 1, "mode": "unsafe"}
        )
        self.assertFalse(result["ok"])
        function.assert_not_called()

    def test_tool_exception_is_a_failed_result_with_reason(self):
        function = mock.Mock(side_effect=RuntimeError("database unavailable"))
        result = ToolExecutor(_registry_with(function)).execute("sample", {"value": 1})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "tool_execution_error")
        self.assertEqual(result["error"]["message"], "database unavailable")
        self.assertEqual(result["error"]["details"]["exception_type"], "RuntimeError")
        function.assert_called_once()

    def test_explicit_business_failure_is_not_relabelled_as_success(self):
        business_result = {"ok": False, "reason": "incomplete query"}
        result = ToolExecutor(_registry_with(lambda **_: business_result)).execute(
            "sample", {"value": 1}
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "business_tool_failed")
        self.assertEqual(result["error"]["details"]["business_result"], business_result)

    def test_unserializable_tool_result_returns_explicit_failure(self):
        result = ToolExecutor(_registry_with(lambda **_: {"value": object()})).execute(
            "sample", {"value": 1}
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "result_serialization_error")


class BuiltInSchemaTests(unittest.TestCase):
    def test_invalid_and_malformed_dates_are_rejected(self):
        for bad_date in ("2026-02-30", "2026/02/28", "2026-2-8"):
            with self.subTest(date=bad_date):
                with self.assertRaises(SchemaValidationError):
                    ACTIVATION_SCHEMA.validate({
                        "cohort_start": bad_date,
                        "cohort_end": "2026-03-01",
                    })

    def test_retention_array_type_items_and_duplicates_are_validated(self):
        base = {"cohort_start": "2026-01-01", "cohort_end": "2026-01-10"}
        for bad_days in ("1,7", [1, "7"], [1, 1], [], [True], [[]]):
            with self.subTest(days=bad_days):
                with self.assertRaises(SchemaValidationError):
                    RETENTION_SCHEMA.validate({**base, "retention_days": bad_days})

    def test_each_breakdown_whitelist_is_distinct(self):
        dates = {"start_date": "2026-01-01", "end_date": "2026-01-02"}
        ACTIVITY_SCHEMA.validate({**dates, "breakdown_by": "plan_type"})
        FEEDBACK_SCHEMA.validate({**dates, "breakdown_by": "channel"})
        with self.assertRaises(SchemaValidationError):
            ACTIVITY_SCHEMA.validate({**dates, "breakdown_by": "channel"})
        with self.assertRaises(SchemaValidationError):
            FEEDBACK_SCHEMA.validate({**dates, "breakdown_by": "region"})

    def test_feedback_modes_and_integer_ranges_are_validated(self):
        dates = {"start_date": "2026-01-01", "end_date": "2026-01-02"}
        for updates in (
            {"analysis_mode": "llm"},
            {"page_size": 0},
            {"page_size": 201},
            {"top_n": 0},
        ):
            with self.subTest(updates=updates):
                with self.assertRaises(SchemaValidationError):
                    FEEDBACK_SCHEMA.validate({**dates, **updates})

    def test_optional_defaults_match_public_business_signatures(self):
        activation = ACTIVATION_SCHEMA.validate({
            "cohort_start": "2026-01-01", "cohort_end": "2026-01-02"
        })
        retention = RETENTION_SCHEMA.validate({
            "cohort_start": "2026-01-01", "cohort_end": "2026-01-02"
        })
        feedback = FEEDBACK_SCHEMA.validate({
            "start_date": "2026-01-01", "end_date": "2026-01-02"
        })
        self.assertEqual(activation["observation_days"], 7)
        self.assertEqual(retention["retention_days"], [1, 7, 30])
        self.assertEqual(feedback["analysis_mode"], "rules")
        self.assertEqual(feedback["page_size"], 100)
        self.assertIsNone(retention["breakdown_by"])


class ToolSystemIntegrationTests(unittest.TestCase):
    def test_schema_parameter_names_follow_current_public_signatures(self):
        cases = (
            (analyze_activation_funnel, ACTIVATION_SCHEMA),
            (analyze_retention, RETENTION_SCHEMA),
            (analyze_user_activity, ACTIVITY_SCHEMA),
            (analyze_feedback_trends, FEEDBACK_SCHEMA),
        )
        for function, schema in cases:
            with self.subTest(function=function.__name__):
                source_names = set(inspect.signature(function).parameters) - {
                    "db_path", "query_runner"
                }
                schema_names = {parameter.name for parameter in schema.parameters}
                self.assertEqual(schema_names, source_names)

    def test_default_registry_exposes_all_schemas_without_database_access(self):
        descriptions = create_default_tool_registry().get_tool_descriptions()
        self.assertEqual(len(descriptions), 4)
        for description in descriptions:
            self.assertTrue(description["parameters"]["required"])
            self.assertFalse(description["parameters"]["additionalProperties"])

    def test_four_tool_shapes_execute_through_one_entry_point_with_mocks(self):
        cases = (
            ("analyze_activation_funnel", ACTIVATION_SCHEMA,
             {"cohort_start": "2026-01-01", "cohort_end": "2026-01-02"}),
            ("analyze_retention", RETENTION_SCHEMA,
             {"cohort_start": "2026-01-01", "cohort_end": "2026-01-02"}),
            ("analyze_user_activity", ACTIVITY_SCHEMA,
             {"start_date": "2026-01-01", "end_date": "2026-01-02"}),
            ("analyze_feedback_trends", FEEDBACK_SCHEMA,
             {"start_date": "2026-01-01", "end_date": "2026-01-02"}),
        )
        registry = ToolRegistry()
        functions = {}
        for name, schema, _ in cases:
            functions[name] = mock.Mock(return_value={"ok": True, "source": name})
            registry.register_tool(ToolDefinition(
                name, "模拟业务描述", "验证统一入口", schema, functions[name], True
            ))
        executor = ToolExecutor(registry)
        for name, _, arguments in cases:
            with self.subTest(tool=name):
                result = executor.execute(name, arguments)
                self.assertTrue(result["ok"])
                self.assertEqual(result["data"]["source"], name)
                functions[name].assert_called_once()


if __name__ == "__main__":
    unittest.main()
