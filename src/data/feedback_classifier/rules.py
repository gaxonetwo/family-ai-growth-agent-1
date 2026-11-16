"""Immutable, explicitly ordered local feedback rules (no sentence lookup)."""
from __future__ import annotations

from dataclasses import dataclass
import re

RULE_VERSION = "1.0.0"
CATEGORIES = (
    "device_connection", "onboarding", "performance",
    "feature_discovery", "general_experience",
)
SENTIMENTS = ("positive", "negative", "neutral")


@dataclass(frozen=True)
class Pattern:
    expression: str
    match_kind: str = "specific_phrase"


@dataclass(frozen=True)
class Rule:
    rule_id: str
    label: str
    priority: int
    patterns: tuple[Pattern, ...]
    overlap: bool = False


def phrase(expression: str) -> Pattern:
    return Pattern(expression)


def combination(expression: str) -> Pattern:
    return Pattern(expression, "keyword_combination")


CATEGORY_RULES = (
    Rule("C.OVERLAP.SETUP_GUIDANCE", "device_connection", 0, (
        combination(r"配网[^。！？!?；;]{0,40}?(?:指引|操作说明)"),
    ), True),
    Rule("C.OVERLAP.POST_SIGNUP", "onboarding", 1, (
        combination(r"注册[^。！？!?；;]{0,20}?(?:不知道|不清楚)[^。！？!?；;]{0,12}?(?:下一步|接下来)"),
    ), True),
    Rule("C.OVERLAP.FEATURE_HINT", "feature_discovery", 2, (
        combination(r"功能介绍[^。！？!?；;]{0,20}?使用提示"),
    ), True),
    Rule("C.DEVICE.SETUP", "device_connection", 3, (phrase(r"配网"),)),
    Rule("C.DEVICE.CONNECTION", "device_connection", 4, (
        phrase(r"设备\s*连接|设备\s*离线|重连"),
        combination(r"设备[^。！？!?；;，,]{0,12}?(?:离线|掉线|断线|断开)"),
    )),
    Rule("C.ONBOARDING.NEXT_STEP", "onboarding", 5, (
        combination(r"注册[^。！？!?；;]{0,20}?(?:下一步|接下来)"),
    )),
    Rule("C.ONBOARDING.FIRST_USE", "onboarding", 6, (
        phrase(r"新手\s*(?:引导|教程)|第一次\s*(?:使用|用)|首次\s*(?:使用|注册)"),
    )),
    Rule("C.PERFORMANCE.LOAD", "performance", 7, (
        phrase(r"页面\s*加载|加载\s*速度|页面\s*卡顿"),
        combination(r"页面[^。！？!?；;，,]{0,10}?加载"),
    )),
    Rule("C.PERFORMANCE.RESPONSE", "performance", 8, (
        phrase(r"响应\s*时间|操作\s*响应"),
        combination(r"操作[^。！？!?；;，,]{0,20}?响应"),
    )),
    Rule("C.PERFORMANCE.FLUENCY", "performance", 9, (
        phrase(r"运行\s*流畅度"),
        combination(r"(?:应用|运行)[^。！？!?；;，,]{0,12}?流畅"),
    )),
    Rule("C.FEATURE.ENTRY", "feature_discovery", 10, (
        phrase(r"功能介绍入口|功能入口"),
        combination(r"功能[^。！？!?；;，,]{0,8}?入口|入口[^。！？!?；;，,]{0,8}?功能"),
        combination(r"首页[^。！？!?；;，,]{0,16}?功能|功能[^。！？!?；;，,]{0,12}?首页"),
    )),
    Rule("C.FEATURE.DESCRIPTION", "feature_discovery", 11, (
        phrase(r"功能介绍|使用提示"),
    )),
    Rule("C.GENERAL.EXPERIENCE", "general_experience", 12, (
        phrase(r"整体\s*(?:使用)?体验|总体\s*体验|操作\s*流程|功能\s*更新"),
    )),
)

SENTIMENT_RULES = (
    Rule("S.POSITIVE.PRAISE", "positive", 0, (
        phrase(r"不错|很棒|很好用|很好|比较方便|挺方便|顺手|实用|清晰|很清楚|很容易|容易找到|稳定|放心|顺利|很流畅|很快"),
    )),
    Rule("S.NEGATIVE.PROBLEM", "negative", 1, (
        phrase(r"不太清楚|不清楚|不知道|不够明显|不太顺畅|不顺畅|不好用|不稳定|不方便|不清晰|不流畅|不顺手|不实用|不满意|不直观|不好找|无法连接|连不上|很差|太复杂|太贵|失败|中断|掉线|断线|断开|离线|卡住|卡顿|太难找|很难找|比较慢|太慢|(?<!不)慢|较长|等很久|等半天"),
    )),
    Rule("S.NEUTRAL.SUGGESTION", "neutral", 2, (
        phrase(r"希望|建议|需要|请"),
    )),
    Rule("S.NEUTRAL.OBSERVATION", "neutral", 3, (
        combination(r"没有注意到[^。！？!?；;]{0,12}?功能入口"),
    )),
    Rule("S.UNKNOWN.CONTEXT", "unknown", 4, (
        phrase(r"不算不|不是不|并非不|不太不|不是没有|难道|呵呵|真是够了"),
        combination(r"(?:没|没有|不再|不会)[^。！？!?；;，,]{0,6}?(?:掉线|断线|中断|失败|卡顿)"),
        combination(r"以前[^。！？!?；;]{0,30}?现在"),
        combination(r"[“\"][^。！？!?；;]{1,60}?[”\"]"),
    )),
)


def find_matches(document, rules: tuple[Rule, ...], dimension: str) -> list[dict]:
    """Find every evidence occurrence and map its span to original text."""
    matches = []
    seen = set()
    for rule in rules:
        for pattern in rule.patterns:
            for match in re.finditer(pattern.expression, document.text):
                start, end = document.original_span(*match.span())
                key = (rule.rule_id, pattern.match_kind, start, end)
                if key in seen:
                    continue
                seen.add(key)
                matches.append({
                    "dimension": dimension, "rule_id": rule.rule_id,
                    "match_kind": pattern.match_kind,
                    "matched_text": document.original[start:end],
                    "start": start, "end": end,
                    "label": rule.label, "priority": rule.priority,
                    "overlap": rule.overlap,
                    "normalized_start": match.start(), "normalized_end": match.end(),
                })
    return matches


def decision_key(match: dict) -> tuple:
    """Explicit decision order, independent of container iteration order."""
    return (
        0 if match["overlap"] else 1,
        0 if match["match_kind"] == "specific_phrase" else 1,
        match["priority"], match["start"], -len(match["matched_text"]),
        match["rule_id"], match["matched_text"],
    )
