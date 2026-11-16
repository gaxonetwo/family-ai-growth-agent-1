"""Conservative, local report contract. Unknown prose is not semantic proof.

The model selects facts and follow-up questions; it cannot invent metric labels,
formulas or conclusions. Claim templates remain deliberately narrow.
"""
import json
import math
import re


SUGGESTIONS = (
    "建议进一步分析不同获客渠道的差异。",
    "建议进一步观察更长时间窗口的数据。",
    "建议进一步验证产品体验与使用行为是否存在关联。",
    "建议进一步分析当前套餐分组的差异。",
)


def report_context(tool_name, data):
    """Build definitions from actual output paths, without querying data again."""
    catalog = {}

    def add(path, label):
        current = data
        try:
            for part in path.strip("/").split("/"):
                current = current[int(part)] if isinstance(current, list) else current[part]
        except (KeyError, IndexError, TypeError, ValueError):
            return
        if current is None or (type(current) in (int, float) and math.isfinite(current)):
            catalog[path] = label

    if tool_name == "analyze_user_activity":
        labels = {"dau": "DAU（单日独立活跃用户数）", "wau": "WAU（含当日的滚动7日独立活跃用户数）",
                  "mau": "MAU（含当日的滚动30日独立活跃用户数）", "event_records": "有效事件记录数（未按用户去重）"}
        for metric, label in labels.items():
            add("/" + metric, label)
            for i, row in enumerate(data.get("time_series", [])):
                add(f"/time_series/{i}/{metric}", f"{row['date']}的{label}")
        for metric in ("dau", "wau", "mau"):
            add(f"/summary/average_{metric}", f"周期内可用日期的平均{labels[metric]}")
            add(f"/summary/peak_{metric}/users", f"周期内{labels[metric]}峰值")
    elif tool_name == "analyze_activation_funnel":
        add("/activation/activation_rate", "严格顺序窗口内激活人数/成熟注册人数（0至1）")
        add("/activation/activated_users", "严格顺序窗口内激活用户数")
        for key in data.get("counts", {}):
            add("/counts/" + key, f"激活漏斗独立用户数[{key}]")
        for key in data.get("conversion_rates", {}):
            add("/conversion_rates/" + key, f"漏斗相邻环节转化率[{key}]（0至1）")
    elif tool_name == "analyze_retention":
        for day in data.get("metrics", {}):
            if re.fullmatch(r"D[1-9]\d*", day):
                add(f"/metrics/{day}/retention_rate", f"{day}精确日期留存率（共同成熟用户作分母，0至1）")
                add(f"/metrics/{day}/retained_users", f"{day}精确日期留存独立用户数")
        for key in ("registered_users", "eligible_users", "excluded_users"):
            add("/" + key, f"留存样本用户数[{key}]")
    elif tool_name == "analyze_feedback_trends":
        for key in data.get("summary", {}):
            add("/summary/" + key, f"反馈统计[{key}]（unknown不等于neutral；规则标签不代表真实满意度）")
        for kind in ("category_distribution", "sentiment_distribution", "classification_status_distribution"):
            for i, row in enumerate(data.get(kind) or []):
                for field in ("count", "rate"):
                    add(f"/{kind}/{i}/{field}", f"反馈规则分类[{kind}:{row['name']}:{field}]（占比分母含unknown及invalid_input）")
    restrictions = ["本报告仅支持已引用的观测值及首尾比较；不能证明持续趋势或因果关系。"]
    definitions = {
        "analyze_user_activity": "活跃按app_open或ai_interaction独立用户统计；DAU/WAU/MAU不可替代留存。平均值只含可用日期，窗口不足为null而非0。",
        "analyze_activation_funnel": "仅成熟注册用户进入分母；注册<=设备绑定<=新手引导<=AI交互，全部在注册观察窗口内；整体激活率不同于相邻环节转化率。",
        "analyze_retention": "按UTC注册日期加指定天数的精确日期留存；有效事件为app_open或ai_interaction；按最大留存日使用共同成熟用户分母。",
        "analyze_feedback_trends": "统计单位包括反馈记录、独立反馈用户和不同原文，不能互换；情感为规则标签，unknown不等于neutral，不代表真实满意度；套餐为当前快照。",
    }
    if tool_name in definitions:
        restrictions.append(definitions[tool_name])
    methodology = data.get("methodology", {})
    if isinstance(methodology, dict):
        restrictions += [f"methodology.{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in methodology.items()]
    if isinstance(data.get("trend_analysis"), dict) and data["trend_analysis"].get("limitation"):
        restrictions.append(data["trend_analysis"]["limitation"])
    return dict(metric_catalog=catalog, required_limitations=restrictions,
                allowed_suggestions=list(SUGGESTIONS))


def multi_report_context(tool_results):
    """Combine existing per-tool metric definitions under traceable call paths."""
    catalog = {}
    sources = {}
    limitations = [
        "本综合报告只使用列出的成功工具结果；不能证明指标之间存在相关性或因果关系。",
        "不同工具可能使用不同时间窗口、样本成熟度、分母和统计单位，未经专门计算不得直接比较。",
    ]
    for index, item in enumerate(tool_results):
        call_index = item["call_index"]
        tool_name = item["tool_name"]
        context = report_context(tool_name, item["data"])
        prefix = f"/tool_results/{index}/data"
        for path, label in context["metric_catalog"].items():
            combined_path = prefix + path
            catalog[combined_path] = f"调用{call_index} {tool_name}｜{label}"
            sources[combined_path] = dict(call_index=call_index, tool_name=tool_name)
        limitations.extend(
            f"调用{call_index} {tool_name}：{value}"
            for value in context["required_limitations"]
        )
    return dict(metric_catalog=catalog, metric_sources=sources,
                required_limitations=limitations,
                allowed_suggestions=list(SUGGESTIONS))


class SemanticReportError(ValueError):
    """Stable local rejection reason, never contains model or user prose."""


def validate_semantics(report, data, context, resolve_path):
    """Exact templates deliberately reject unrecognized semantic assertions."""
    def reject(code):
        raise SemanticReportError(code)

    if set(report) != {"status", "summary", "summary_finding_indices", "summary_evidence", "key_findings", "limitations", "suggestions"}:
        reject("invalid_semantic_report_fields")
    descriptions = []
    for finding in report["key_findings"]:
        claim = finding.get("claim")
        if not isinstance(claim, dict):
            reject("unsupported_semantic_claim")
        if set(finding) != {"title", "description", "claim", "evidence"}:
            reject("invalid_semantic_finding_fields")
        evidence = finding["evidence"]
        if claim.get("kind") == "observed":
            if set(claim) != {"kind", "path"}:
                reject("unsupported_semantic_claim")
            path = claim.get("path")
            label = context["metric_catalog"].get(path)
            if label is None:
                reject("unsupported_metric_definition")
            value = resolve_path(data, path)
            expected = f"{label}：{json.dumps(value, ensure_ascii=False, allow_nan=False)}。"
            expected_evidence = [{"json_path": path, "value": value}]
            title = label
        elif claim.get("kind") == "endpoint_change":
            if set(claim) != {"kind", "formula", "value"}:
                reject("unsupported_calculation")
            paths = ("/trend_analysis/first_dau", "/trend_analysis/last_dau", "/trend_analysis/change_rate")
            if "/dau" not in context["metric_catalog"]:
                reject("unsupported_calculation")
            first, last, rate = [resolve_path(data, p) for p in paths]
            if any(type(v) not in (int, float) or not math.isfinite(v) for v in (first, last, rate)) or first <= 0:
                reject("unsupported_calculation")
            if rate != round((last - first) / first, 4) or claim.get("formula") != "(last-first)/first" or type(claim.get("value")) not in (int, float) or claim.get("value") != rate:
                reject("calculation_mismatch")
            start, end = data.get("start_date"), data.get("end_date")
            if not isinstance(start, str) or not isinstance(end, str) or start >= end:
                reject("unsupported_calculation")
            expected = f"周期首尾比较（{start}至{end}）：最后一天DAU相对第一天变化率为{rate}（小数比例，不代表持续趋势或因果）。"
            title = "DAU首尾变化"
            expected_evidence = [{"json_path": p, "value": v} for p, v in zip(paths, (first, last, rate))]
            expected_evidence += [{"json_path": "/start_date", "value": start}, {"json_path": "/end_date", "value": end}]
        else:
            reject("unsupported_semantic_claim")
        if finding["description"] != expected or finding["title"] != title:
            reject("unverified_semantic_statement")
        if evidence != expected_evidence:
            reject("claim_evidence_mismatch")
        descriptions.append(expected)
    # Summary is a traceable subset of verified findings, never a second channel
    # for unconstrained business claims. CLI keeps its original summary string.
    indices = report.get("summary_finding_indices")
    if not isinstance(indices, list) or not indices or any(type(i) is not int or i < 0 or i >= len(descriptions) for i in indices):
        reject("missing_summary_evidence")
    if report["summary"] != "\n".join(descriptions[i] for i in indices):
        reject("unverified_summary")
    expected_summary = [item for i in indices for item in report["key_findings"][i]["evidence"]]
    if report.get("summary_evidence") != expected_summary:
        reject("summary_evidence_mismatch")
    if any(s not in SUGGESTIONS for s in report["suggestions"]):
        reject("unverified_suggestion")
    if report["limitations"] != context["required_limitations"]:
        reject("methodology_limits_missing_or_changed")


def validate_multi_semantics(report, data, context, resolve_path):
    """Validate observed and cross-tool claims without inferring relationships."""
    def reject(code):
        raise SemanticReportError(code)

    expected_fields = {"status", "summary", "summary_finding_indices",
                       "summary_evidence", "key_findings", "limitations",
                       "suggestions"}
    if set(report) != expected_fields:
        reject("invalid_semantic_report_fields")
    descriptions = []
    for finding in report["key_findings"]:
        if set(finding) != {"title", "description", "claim", "evidence"}:
            reject("invalid_semantic_finding_fields")
        claim = finding.get("claim")
        if not isinstance(claim, dict):
            reject("unsupported_semantic_claim")
        kind = claim.get("kind")
        if kind == "observed":
            if set(claim) != {"kind", "path"}:
                reject("unsupported_semantic_claim")
            path = claim.get("path")
            label = context["metric_catalog"].get(path)
            if label is None:
                reject("unsupported_metric_definition")
            value = resolve_path(data, path)
            title = label
            description = f"{label}：{json.dumps(value, ensure_ascii=False, allow_nan=False)}。"
            evidence = [{"json_path": path, "value": value}]
        elif kind == "cross_tool_observation":
            if set(claim) != {"kind", "paths"} or not isinstance(claim.get("paths"), list):
                reject("unsupported_semantic_claim")
            paths = claim["paths"]
            if len(paths) != 2 or len(set(paths)) != 2:
                reject("invalid_cross_tool_claim")
            if any(path not in context["metric_catalog"] for path in paths):
                reject("unsupported_metric_definition")
            sources = [context["metric_sources"][path] for path in paths]
            if sources[0]["tool_name"] == sources[1]["tool_name"]:
                reject("cross_tool_claim_requires_distinct_tools")
            labels = [context["metric_catalog"][path] for path in paths]
            values = [resolve_path(data, path) for path in paths]
            title = "跨工具指标并列观察（不代表相关或因果）"
            description = (
                f"跨工具并列观察：{labels[0]}为"
                f"{json.dumps(values[0], ensure_ascii=False, allow_nan=False)}；"
                f"{labels[1]}为{json.dumps(values[1], ensure_ascii=False, allow_nan=False)}。"
                "这些指标口径不同，仅表示本次结果同时出现，不代表相关性或因果关系。"
            )
            evidence = [
                {"json_path": path, "value": value}
                for path, value in zip(paths, values)
            ]
        else:
            reject("unsupported_semantic_claim")
        if finding["title"] != title or finding["description"] != description:
            reject("unverified_semantic_statement")
        if finding["evidence"] != evidence:
            reject("claim_evidence_mismatch")
        descriptions.append(description)

    indices = report.get("summary_finding_indices")
    if (not isinstance(indices, list) or not indices or
            any(type(index) is not int or index < 0 or index >= len(descriptions)
                for index in indices)):
        reject("missing_summary_evidence")
    if report["summary"] != "\n".join(descriptions[index] for index in indices):
        reject("unverified_summary")
    expected_summary = [
        item for index in indices for item in report["key_findings"][index]["evidence"]
    ]
    if report.get("summary_evidence") != expected_summary:
        reject("summary_evidence_mismatch")

    for suggestion in report["suggestions"]:
        if (not isinstance(suggestion, dict) or
                set(suggestion) != {"text", "based_on_finding_indices"} or
                suggestion.get("text") not in SUGGESTIONS):
            reject("unverified_suggestion")
        based_on = suggestion.get("based_on_finding_indices")
        if (not isinstance(based_on, list) or not based_on or
                any(type(index) is not int or index < 0 or index >= len(descriptions)
                    for index in based_on)):
            reject("suggestion_missing_evidence")
    if report["limitations"] != context["required_limitations"]:
        reject("methodology_limits_missing_or_changed")
