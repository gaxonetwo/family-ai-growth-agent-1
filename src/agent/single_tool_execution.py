"""Explicit single-tool handoff from intent validation to ToolExecutor."""
from src.agent.intent.intent_pipeline import IntentPipelineResult
from src.agent.intent.models import IntentCandidate, IntentStatus
from src.agent.intent.tool_matcher import INTENT_TOOL_MAP
from src.agent.tool_executor import ToolExecutor
from src.agent.tool_registry import ToolNotFoundError
from src.agent.tool_schemas import SchemaValidationError


class SingleToolExecution:
    """Accept only locally validated task snapshots; never retry execution."""

    def __init__(self, executor: ToolExecutor):
        if not isinstance(executor, ToolExecutor):
            raise TypeError("executor must be a ToolExecutor instance.")
        self.executor = executor

    def execute(self, result: IntentPipelineResult) -> dict:
        if not isinstance(result, IntentPipelineResult):
            raise TypeError("result must be an IntentPipelineResult.")
        task = result.to_dict()

        def blocked(code, message):
            return dict(ok=False, intent_result=task, execution_status="blocked",
                        tool_result=None, error=dict(code=code, message=message))

        match, validation, dates = task["tool_match"], task["validation"], task["date_resolution"]
        if result.status is not IntentStatus.READY or task["error"] is not None:
            return blocked("intent_not_ready", "任务尚未验证通过，禁止执行。")
        if not match or not validation or not dates or any(
            stage.get("status") != "ready" for stage in (match, validation, dates)
        ):
            return blocked("invalid_validation", "匹配、日期或参数验证结果不完整。")
        if task["clarification_question"] or validation.get("conflict_details") or validation.get("validation_errors") or validation.get("missing_fields"):
            return blocked("unresolved_validation", "仍有澄清、冲突或参数错误。")
        name = match.get("tool_name")
        if not isinstance(name, str) or name != validation.get("tool_name"):
            return blocked("tool_mismatch", "匹配工具与验证工具不一致。")
        try:
            intent = IntentCandidate(match.get("intent"))
        except (ValueError, TypeError):
            return blocked("invalid_intent", "匹配意图无效。")
        if INTENT_TOOL_MAP.get(intent) != name or match.get("candidate_tools") != [name]:
            return blocked("tool_mismatch", "工具不符合已匹配意图。")
        arguments = validation.get("arguments")
        if not isinstance(arguments, dict):
            return blocked("invalid_arguments", "最终参数必须为字典。")
        try:
            definition = self.executor.registry.get_tool(name)
            if not definition.read_only:
                return blocked("tool_not_read_only", "本阶段仅允许只读业务工具。")
            checked = definition.schema.validate(arguments)
        except ToolNotFoundError:
            return blocked("tool_not_found", "工具已不在Registry中。")
        except SchemaValidationError as exc:
            return blocked("argument_validation_error", str(exc))
        if checked != arguments:
            return blocked("incomplete_validated_arguments", "最终参数缺少Schema默认值，请重新验证。")

        # Never derive arguments from the model draft or generate SQL here.
        execution = self.executor.execute(name, arguments)
        success = execution.get("ok") is True
        return dict(ok=success, intent_result=task,
                    execution_status="success" if success else "failed",
                    tool_result=execution, error=None if success else execution.get("error"))
