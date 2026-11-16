"""Single-tool execution tests with registered fake callables, no business DB."""
from dataclasses import replace
import socket
import sqlite3
import unittest
from unittest.mock import Mock, patch

from src.agent.single_tool_execution import SingleToolExecution
from src.agent.tool_executor import ToolExecutor
from src.agent.tool_registry import create_default_tool_registry
from src.agent.intent.intent_pipeline import IntentPipeline
from src.agent.intent.models import IntentStatus
from src.tools import demo_intent as cli


class SingleToolTests(unittest.TestCase):
    def setUp(self):
        self.registry = create_default_tool_registry()
        self.function = Mock(return_value={"ok": True, "users": 12, "optional": None})
        definition = self.registry.unregister_tool("analyze_user_activity")
        self.registry.register_tool(replace(definition, execute=self.function))
        self.pipeline = IntentPipeline(cli.create_demo_provider(), self.registry,
            environ={"INTENT_CONFIDENCE_THRESHOLD": "0.8"})
        self.result = self.pipeline.process("分析最近7天活跃")
        self.executor = ToolExecutor(self.registry)
        self.bridge = SingleToolExecution(self.executor)

    def test_success_and_exact_arguments(self):
        with patch.object(self.executor, "execute", wraps=self.executor.execute) as execute:
            output = self.bridge.execute(self.result)
        expected = self.result.to_dict()["validation"]["arguments"]
        execute.assert_called_once_with("analyze_user_activity", expected)
        self.function.assert_called_once_with(**expected)
        self.assertTrue(output["ok"])
        self.assertEqual(output["tool_result"]["data"]["users"], 12)
        self.assertIsNone(output["tool_result"]["data"]["optional"])

    def test_non_ready_states_block(self):
        for status in (IntentStatus.INVALID_INPUT, IntentStatus.NEEDS_CLARIFICATION, IntentStatus.UNSUPPORTED):
            with self.subTest(status=status):
                self.assertFalse(self.bridge.execute(replace(self.result, status=status))["ok"])
        self.function.assert_not_called()

    def test_removed_tool_block(self):
        self.registry.unregister_tool("analyze_user_activity")
        self.assertEqual(self.bridge.execute(self.result)["error"]["code"], "tool_not_found")
        self.function.assert_not_called()

    def test_tool_exception_no_retry(self):
        self.function.side_effect = ValueError("fake failure")
        output = self.bridge.execute(self.result)
        self.assertFalse(output["ok"])
        self.assertEqual(output["error"]["code"], "tool_execution_error")
        self.assertEqual(output["execution_status"], "failed")
        self.function.assert_called_once()

    def test_explicit_business_failure(self):
        self.function.return_value = {"ok": False, "reason": "fake failure"}
        self.assertFalse(self.bridge.execute(self.result)["ok"])

    def test_executor_failure_preserved(self):
        failed = {"ok": False, "error": {"code": "fake", "message": "failure"}}
        with patch.object(self.executor, "execute", return_value=failed) as execute:
            output = self.bridge.execute(self.result)
        self.assertFalse(output["ok"])
        self.assertEqual(output["tool_result"], failed)
        execute.assert_called_once()

    def test_mismatched_or_invalid_snapshot_blocked(self):
        for change in ({"tool_name": "analyze_feedback_trends"}, {"arguments": {"unexpected": 1}}, {"status": "needs_clarification"}):
            with self.subTest(change=change):
                validation = {**self.result.to_dict()["validation"], **change}
                self.assertFalse(self.bridge.execute(replace(self.result, validation=validation))["ok"])
        self.function.assert_not_called()

    def test_default_cli_does_not_execute(self):
        with patch.object(cli, "IntentPipeline", return_value=self.pipeline), patch("builtins.input", return_value="分析最近7天活跃"), patch("builtins.print") as printer, patch.object(cli, "SingleToolExecution", side_effect=AssertionError):
            self.assertEqual(cli.main([]), 0)
        self.function.assert_not_called()
        self.assertIn("尚未执行业务工具", str(printer.call_args_list))

    def test_execute_cli_calls_once_no_external_access(self):
        with patch.object(cli, "IntentPipeline", return_value=self.pipeline), patch("builtins.input", return_value="分析最近7天活跃"), patch("builtins.print") as printer, patch.object(sqlite3, "connect", side_effect=AssertionError), patch.object(socket, "create_connection", side_effect=AssertionError):
            self.assertEqual(cli.main(["--execute"]), 0)
        self.function.assert_called_once()
        self.assertIn("工具执行状态：success", str(printer.call_args_list))

    def test_clarification_then_execute_only_final_result(self):
        pipeline = IntentPipeline(cli.create_demo_provider(scenario="clarification"), self.registry,
            environ={"INTENT_CONFIDENCE_THRESHOLD": "0.8"})
        with patch.object(cli, "IntentPipeline", return_value=pipeline), patch("builtins.input", side_effect=["按照套餐分析最近7天活跃", "我指的是用户当前套餐分组"]), patch("builtins.print"):
            self.assertEqual(cli.main(["--execute"]), 0)
        self.function.assert_called_once()
        self.assertEqual(self.function.call_args.kwargs["breakdown_by"], "plan_type")

    def test_unresolved_cli_never_executes(self):
        pipeline = IntentPipeline(cli.create_demo_provider(scenario="unresolved"), self.registry,
            environ={"INTENT_CONFIDENCE_THRESHOLD": "0.8"})
        with patch.object(cli, "IntentPipeline", return_value=pipeline), patch("builtins.input", side_effect=["按照套餐分析最近7天活跃", "是的"]), patch("builtins.print"):
            self.assertEqual(cli.main(["--execute"]), 1)
        self.function.assert_not_called()


if __name__ == "__main__":
    unittest.main()
