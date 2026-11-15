"""Conservative conflict decisions; priority never hides independent topics."""
from __future__ import annotations

import re

from .rules import CATEGORIES, SENTIMENTS, decision_key


def resolve_category(matches: list[dict], text: str = "") -> dict:
    candidates = [label for label in CATEGORIES if any(m["label"] == label for m in matches)]
    base = {"candidates": candidates, "ambiguity_reasons": [], "conflict_reasons": []}
    if not candidates:
        return {**base, "label": "unknown", "rule": "C.UNKNOWN.UNSUPPORTED",
                "reason": "no_supported_topic"}
    if len(candidates) > 1:
        # Separate evidence intervals support independent objects. Overlapping
        # cross-topic evidence is uncertain unless an approved local overlap
        # was already resolved by the category matcher.
        def has_proposition(match):
            left = re.split(r"[。！？!?；;，,]|而且|但是|不过", text[:match["normalized_start"]])[-1]
            right = re.split(r"[。！？!?；;，,]|而且|但是|不过", text[match["normalized_end"]:])[0]
            clause = left + text[match["normalized_start"]:match["normalized_end"]] + right
            return bool(re.search(
                r"希望|建议|需要|请|不错|很好|稳定|清晰|清楚|容易|顺利|流畅|实用|方便|"
                r"失败|中断|掉线|离线|慢|卡顿|卡住|复杂|很差|较长|不好|不知道|不够|不顺畅",
                clause,
            ))

        disjoint = any(
            a["label"] != b["label"] and
            (a["end"] <= b["start"] or b["end"] <= a["start"]) and
            has_proposition(a) and has_proposition(b)
            for a in matches for b in matches
        )
        return {
            **base, "label": "unknown",
            "rule": "C.UNKNOWN.MULTIPLE_TOPICS" if disjoint else "C.UNKNOWN.AMBIGUOUS",
            "reason": "multiple_independent_topics" if disjoint else "ambiguous_topic",
            "conflict_reasons": ["存在不同主题对象及评价/问题/请求的独立证据，不能按优先级覆盖。"
                                 if disjoint else "主题证据重叠，无法可靠确定唯一对象。"],
        }
    chosen = min(matches, key=decision_key)
    ambiguity = []
    if chosen["rule_id"].startswith("C.OVERLAP."):
        ambiguity.append("按已确认的同对象重叠规则解释上下文：" + chosen["rule_id"])
    return {**base, "label": candidates[0], "rule": chosen["rule_id"],
            "reason": None, "ambiguity_reasons": ambiguity}


def resolve_sentiment(matches: list[dict]) -> dict:
    candidates = [label for label in SENTIMENTS if any(m["label"] == label for m in matches)]
    base = {"candidates": candidates, "ambiguity_reasons": [], "conflict_reasons": []}
    if any(m["rule_id"] == "S.UNKNOWN.CONTEXT" for m in matches):
        return {**base, "label": "unknown", "rule": "S.UNKNOWN.CONTEXT",
                "reason": "unsupported_context",
                "ambiguity_reasons": ["复杂否定、引用或时间转折超出首版消解范围。"]}
    if "positive" in candidates and "negative" in candidates:
        return {**base, "label": "unknown", "rule": "S.UNKNOWN.CONFLICT",
                "reason": "conflicting_sentiment",
                "conflict_reasons": ["同时存在明确肯定和明确问题，首版不选择其一。"]}
    for label, rule in (("negative", "S.NEGATIVE.PROBLEM"),
                        ("positive", "S.POSITIVE.PRAISE"),
                        ("neutral", "S.NEUTRAL.SUGGESTION")):
        if label in candidates:
            if label == "neutral" and not any(m["rule_id"] == rule for m in matches):
                rule = "S.NEUTRAL.OBSERVATION"
            ambiguity = []
            if label == "neutral" and (
                rule == "S.NEUTRAL.OBSERVATION" or
                any(m["matched_text"] == "希望" for m in matches)
            ):
                ambiguity.append("建议或未注意到入口可能隐含不满；依本版口径取neutral，不代表满意。")
            return {**base, "label": label, "rule": rule, "reason": None,
                    "ambiguity_reasons": ambiguity}
    return {**base, "label": "unknown", "rule": "S.UNKNOWN.UNRECOGNIZED",
            "reason": "no_supported_sentiment"}
