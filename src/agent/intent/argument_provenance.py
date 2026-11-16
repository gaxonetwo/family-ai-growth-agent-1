"""Evidence tracking and deterministic Chinese clarification templates."""
from copy import deepcopy
import re

from src.agent.intent.models import IntentStatus


def build_provenance(draft, dates, result, schema=None, user_input=None):
    """Track supplied values without treating model evidence as verified text."""
    records, conflicts = {}, []
    values = {**draft.arguments, **dict(dates.date_arguments), **dict(result.arguments)}
    defaults = schema.to_dict()["properties"] if schema else {}
    for name, value in values.items():
        evidence = draft.argument_evidence.get(name)
        source, derivation, verified = "model_extracted", "模型提取；尚未核验原文", False
        if name in dates.date_arguments:
            source = "date_resolver"
            evidence = draft.date_expression or evidence
            derivation = (
                f"DateResolver规则={dates.resolution_basis}；参考日期={dates.reference_date}；"
                f"参考来源={dates.reference_source}；结果={dates.date_arguments[name]}"
            )
            if dates.resolution_basis in ("relative:recent_7_days", "relative:recent_30_days"):
                offset = 6 if dates.resolution_basis.endswith("7_days") else 29
                derivation += (
                    f"；开始日期由参考日期向前计算{offset}天"
                    if name.endswith("start") or name == "start_date"
                    else "；结束日期使用参考日期"
                )
            verified = bool(evidence and user_input is not None and evidence in user_input)
        elif name not in draft.arguments:
            source, evidence = "schema_default", None
            derivation = "由Tool Schema默认值补充"
            verified = name in defaults and "default" in defaults[name] and defaults[name]["default"] == value
        elif evidence and user_input is not None and evidence in user_input:
            literal = str(value) if value is not None else "null"
            verified = literal in evidence
            if name == "breakdown_by" and value == "plan_type":
                # Explicit, narrow meaning: not a generic 'yes' or model guess.
                if "用户当前套餐分组" in evidence and not re.search(r"不|否|别|而非|不要", evidence):
                    verified = True
            source = "user_explicit" if verified else "model_extracted"
            derivation = "用户原文含有参数字面值" if verified else "原文片段已核实，但参数转换尚未核实"
        records[name] = dict(parameter_name=name, value=deepcopy(value), source=source,
                             evidence=evidence, derivation=derivation, verified=verified)
        if name in draft.arguments and name in dates.date_arguments and draft.arguments[name] != dates.date_arguments[name]:
            conflicts.append(dict(parameter_name=name, kind="value_conflict", alternatives=[
                dict(value=deepcopy(draft.arguments[name]), source="model_extracted", evidence=draft.argument_evidence.get(name)),
                dict(value=dates.date_arguments[name], source="date_resolver", evidence=draft.date_expression)]))
        elif user_input is not None and source != "schema_default" and not verified:
            conflicts.append(dict(parameter_name=name, kind="unverified_evidence", alternatives=[deepcopy(records[name])]))
    return records, conflicts


def clarification(result, draft, dates, conflicts):
    if result.status is IntentStatus.READY:
        return None
    if conflicts:
        first = conflicts[0]
        if first["kind"] == "value_conflict":
            for start, end in (("start_date", "end_date"), ("cohort_start", "cohort_end")):
                original, resolved = dict(draft.arguments), dict(dates.date_arguments)
                left, right = {**resolved, **original}, {**original, **resolved}
                if first["parameter_name"] in (start, end) and all(k in left and k in right for k in (start, end)):
                    return f"你希望使用{left[start]}至{left[end]}，还是{right[start]}至{right[end]}？"
            a, b = first["alternatives"]
            return f"参数{first['parameter_name']}应使用{a['value']}还是{b['value']}？"
        return f"请确认参数{first['parameter_name']}的值及对应的原文依据。"
    if result.missing_fields:
        if any(k in result.missing_fields for k in ("cohort_start", "cohort_end", "start_date", "end_date")):
            if draft.date_expression and "最近" in draft.date_expression:
                return "你希望分析最近多少天的数据？"
            return "你希望分析哪个起始日期至结束日期的数据？"
        return f"请提供参数{result.missing_fields[0]}。"
    if "confidence" in result.reason.lower():
        return "请进一步说明你希望分析的业务问题。"
    if dates.status is not IntentStatus.READY:
        return "请明确要分析的日期范围；使用相对日期时请提供可靠的参考日期。"
    return "请确认分析意图及参数：" + "；".join(result.validation_errors or (result.reason,))
