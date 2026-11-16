"""Bridge action arguments to the existing date resolver and live tool schema.

This module locates date expressions, but never implements date arithmetic.
It does not invent model confidence or run the single-intent validation pipeline.
"""
from copy import deepcopy
import re

from src.agent.intent.date_resolver import TOOL_DATE_FIELDS
from src.agent.intent.models import IntentDraft, IntentStatus
from src.agent.intent.tool_matcher import INTENT_TOOL_MAP, ToolMatchResult
from src.agent.tool_schemas import SchemaValidationError


_RELATIVE = re.compile(r"最近\s*\d+\s*天|本月|上个月|今年")
_DATE_HINT = re.compile(r"\d{4}[-/年]\d{1,2}")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def prepare_action_arguments(definition, resolver, arguments, user_input,
                             reference_date=None, date_expression=None):
    """Return traceable parameters or a clarification; never execute a tool."""
    original = deepcopy(arguments)
    trace = dict(tool_name=definition.name, original_arguments=original,
                 date_expression=None, date_resolution=None, arguments={},
                 normalized_date_placeholders=[], normalized_model_date_fields=[],
                 conflicts=[],
                 status="needs_clarification", error=None,
                 clarification_question=None)

    def clarify(code, question):
        trace.update(error={"code": code}, clarification_question=question)
        return trace

    if date_expression is not None:
        if not date_expression.strip() or date_expression not in user_input:
            return clarify("unverified_date_expression", "请提供本次分析日期对应的用户原文，不能使用模型自行生成的日期表达。")
        expression = date_expression
    else:
        # Explicit legacy arguments remain accepted. Relative user expressions
        # always go through local conversion, even if a model supplied dates.
        relative = list(dict.fromkeys(m.group(0) for m in _RELATIVE.finditer(user_input)))
        if len(relative) > 1 or (relative and _DATE_HINT.search(user_input)):
            return clarify("ambiguous_date_expression", "原始任务包含多个日期范围，请明确本次工具要使用哪一段原文日期。")
        if relative:
            expression = relative[0]
        else:
            # Preserve the legacy contract: an empty action is not implicitly
            # confirmed just because unrelated dates occur somewhere in a task.
            # Absolute ranges can use explicit arguments or an original-text
            # date_expression selecting this call's scope.
            expression = None
    trace["date_expression"] = expression
    fields = TOOL_DATE_FIELDS.get(definition.name)
    resolved_arguments = {}
    placeholder_fields = set()
    if fields is not None:
        schema_names = {p.name for p in definition.schema.parameters}
        if not set(fields) <= schema_names:
            return clarify("unsupported_date_schema", "工具日期字段与已登记的日期解析规则不一致，请检查工具配置。")
        provided_fields = [field for field in fields if field in original]
        if provided_fields and len(provided_fields) != len(fields):
            return clarify("incomplete_date_arguments", "模型只提供了部分起止日期，请补充完整日期范围。")
        # Some models repeat a verified relative expression in both concrete
        # date fields. Those strings are placeholders, not competing dates.
        # Only normalize the complete pair; mixed or ISO values still enter
        # the ordinary conflict check below.
        if (expression is not None and provided_fields and
                all(original[field] == expression for field in fields)):
            placeholder_fields = set(fields)
            trace["normalized_date_placeholders"] = list(fields)
        elif (expression is not None and _RELATIVE.fullmatch(expression) and
              len(provided_fields) == len(fields) and
              all(isinstance(original[field], str) and
                  _ISO_DATE.fullmatch(original[field]) for field in fields) and
              not any(original[field] in user_input for field in fields)):
            # Absolute dates calculated by the model are not user evidence.
            # For a verified relative expression, local calendar rules win.
            placeholder_fields = set(fields)
            trace["normalized_model_date_fields"] = list(fields)
        intent = next(key for key, value in INTENT_TOOL_MAP.items() if value == definition.name)
        # Only DateResolver consumes this adapter. 0.0 is an explicit unused
        # placeholder, not model confidence and never passed to IntentValidator.
        draft = IntentDraft((intent,), deepcopy(arguments), {}, expression, 0.0,
                            "Action adapter for date resolution only; confidence is unused.")
        match = ToolMatchResult(IntentStatus.READY, intent, definition.name,
                                (definition.name,), "Registry-confirmed action tool.")
        try:
            resolved = resolver.resolve(draft, match, reference_date=reference_date)
        except (ValueError, OverflowError):
            # Calendar arithmetic can exceed Python's representable years.
            # Do not let an otherwise syntactically valid date crash the run.
            return clarify("date_out_of_range", "日期计算超出支持范围，请提供有效的绝对日期范围或参考日期。")
        trace["date_resolution"] = resolved.to_dict()
        if resolved.status is not IntentStatus.READY:
            return clarify(resolved.error or "date_resolution_error",
                           "请明确本次分析的日期范围；使用相对日期时需要有效的参考日期或数据截止日期。")
        resolved_arguments = dict(resolved.date_arguments)
    elif expression is not None:
        return clarify("unsupported_date_schema", "该工具尚未登记日期字段映射，请明确工具参数或补充日期配置。")

    merged = {key: deepcopy(value) for key, value in arguments.items()
              if key not in placeholder_fields}
    for key, value in resolved_arguments.items():
        if key in original and key not in placeholder_fields and original[key] != value:
            trace["conflicts"].append(dict(parameter_name=key,
                model_value=deepcopy(original[key]), date_resolver_value=value))
        else:
            merged[key] = value
    if trace["conflicts"]:
        return clarify("date_argument_conflict", "模型日期参数与原文的本地解析结果不一致，请确认：" +
                       "；".join(f"{c['parameter_name']}：{c['model_value']} 或 {c['date_resolver_value']}" for c in trace["conflicts"]) + "。")
    try:
        validated = definition.schema.validate(merged)
        for start, end in (("cohort_start", "cohort_end"), ("start_date", "end_date")):
            if validated.get(start) is not None and validated.get(end) is not None and validated[start] > validated[end]:
                raise SchemaValidationError(f"{start} must not be later than {end}.")
    except SchemaValidationError as exc:
        return clarify("argument_validation_error", "请补充或修正本次工具参数：" + str(exc))
    trace.update(status="ready", arguments=deepcopy(validated))
    return trace
