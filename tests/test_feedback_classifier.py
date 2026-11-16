"""Independent text fixtures; no database, CSV, API or ground-truth access."""
from __future__ import annotations

from collections import Counter
import json
import unittest
from unittest.mock import patch

from src.data.feedback_classifier import classify_feedback_text
from src.data.feedback_classifier.normalizer import normalize_text
from src.data.feedback_classifier.rules import CATEGORY_RULES, SENTIMENT_RULES, RULE_VERSION


ORIGINALS = (
    ("希望在首页更容易找到常用功能。", 22, "feature_discovery", "neutral"),
    ("整体使用体验不错，常用功能比较方便。", 18, "general_experience", "positive"),
    ("操作流程清晰，使用起来比较顺手。", 18, "general_experience", "positive"),
    ("最近的功能更新比较实用。", 16, "general_experience", "positive"),
    ("没有注意到部分功能入口。", 13, "feature_discovery", "neutral"),
    ("页面加载有时比较慢。", 12, "performance", "negative"),
    ("第一次使用时不太清楚从哪里开始。", 12, "onboarding", "negative"),
    ("功能介绍不够明显，需要更多使用提示。", 12, "feature_discovery", "negative"),
    ("完成注册后，不知道下一步应该使用什么功能。", 11, "onboarding", "negative"),
    ("部分操作响应时间较长。", 10, "performance", "negative"),
    ("设备有时显示离线，重新连接后恢复。", 10, "device_connection", "negative"),
    ("配网步骤不太顺畅，希望提供更清晰的指引。", 9, "device_connection", "negative"),
    ("设备连接偶尔中断，希望连接过程更稳定。", 9, "device_connection", "negative"),
    ("希望增加更直观的新手引导。", 7, "onboarding", "neutral"),
    ("希望优化应用运行流畅度。", 7, "performance", "neutral"),
)

EXTRA_CASES = (
    ("设备总是掉线，得反复重连。", "device_connection", "negative"),
    ("设备连接很稳定，用着放心。", "device_connection", "positive"),
    ("配网过程太复杂了，请补一份操作说明。", "device_connection", "negative"),
    ("刚注册完，我就不知道接下来该做什么。", "onboarding", "negative"),
    ("新手教程很清楚，第一次用也能上手。", "onboarding", "positive"),
    ("建议加一段首次使用教程。", "onboarding", "neutral"),
    ("常用功能的入口太难找了。", "feature_discovery", "negative"),
    ("建议把常用功能放到首页。", "feature_discovery", "neutral"),
    ("打开页面等很久，加载速度太慢。", "performance", "negative"),
    ("每次操作都要等半天才响应。", "performance", "negative"),
    ("应用运行很流畅，操作响应也很快。", "performance", "positive"),
    ("希望应用运行得更流畅些。", "performance", "neutral"),
    ("总体体验很棒，日常用着挺方便。", "general_experience", "positive"),
    ("整体体验很差，操作流程也不好用。", "general_experience", "negative"),
    ("设备一直掉线，而且页面加载很慢。", "unknown", "negative"),
    ("设备连接很稳定，但偶尔也会断开。", "device_connection", "unknown"),
    ("配网很容易，但是首次注册的下一步指引很差。", "unknown", "unknown"),
    ("以前页面加载慢，现在很好用。", "performance", "unknown"),
    ("价格太贵了。", "unknown", "negative"),
    ("今天天气怎么样？", "unknown", "unknown"),
    ("", "unknown", "unknown"),
    ("设备连接不算不稳定吧。", "device_connection", "unknown"),
)

BOUNDARY_CASES = (
    ("设备连接稳定，重连也很顺利。", "device_connection", "positive", "C.DEVICE.CONNECTION", "S.POSITIVE.PRAISE"),
    ("设备连接失败，反复断线。", "device_connection", "negative", "C.DEVICE.CONNECTION", "S.NEGATIVE.PROBLEM"),
    ("页面加载很快，体验很好。", "performance", "positive", "C.PERFORMANCE.LOAD", "S.POSITIVE.PRAISE"),
    ("页面加载太慢，还经常卡住。", "performance", "negative", "C.PERFORMANCE.LOAD", "S.NEGATIVE.PROBLEM"),
    ("首页的功能入口很清楚，常用功能很容易找到。", "feature_discovery", "positive", "C.FEATURE.ENTRY", "S.POSITIVE.PRAISE"),
    ("建议增加一个功能介绍入口。", "feature_discovery", "neutral", "C.FEATURE.ENTRY", "S.NEUTRAL.SUGGESTION"),
    ("设备连接失败，而且页面加载太慢。", "unknown", "negative", "C.UNKNOWN.MULTIPLE_TOPICS", "S.NEGATIVE.PROBLEM"),
    ("设备连接很稳定，但也经常断线。", "device_connection", "unknown", "C.DEVICE.CONNECTION", "S.UNKNOWN.CONFLICT"),
)


class FeedbackClassifierTests(unittest.TestCase):
    def assert_labels(self, text, category, sentiment):
        result = classify_feedback_text(text)
        self.assertEqual(result["category"], category, text)
        self.assertEqual(result["sentiment"], sentiment, text)
        return result

    def test_all_15_original_texts(self):
        for text, _, category, sentiment in ORIGINALS:
            with self.subTest(text=text):
                self.assert_labels(text, category, sentiment)

    def test_all_22_extra_cases(self):
        for text, category, sentiment in EXTRA_CASES:
            with self.subTest(text=text):
                self.assert_labels(text, category, sentiment)

    def test_all_8_boundary_cases_and_rule_ids(self):
        for text, category, sentiment, c_rule, s_rule in BOUNDARY_CASES:
            with self.subTest(text=text):
                result = self.assert_labels(text, category, sentiment)
                self.assertEqual(result["category_rule"], c_rule)
                self.assertEqual(result["sentiment_rule"], s_rule)
                self.assertEqual(result["is_ambiguous"], text in (BOUNDARY_CASES[6][0], BOUNDARY_CASES[7][0]))

    def test_original_frequency_reconciliation(self):
        topics, sentiments = Counter(), Counter()
        for text, count, _, _ in ORIGINALS:
            result = classify_feedback_text(text)
            topics[result["category"]] += count
            sentiments[result["sentiment"]] += count
        self.assertEqual(sum(topics.values()), 186)
        self.assertEqual(dict(topics), {
            "general_experience": 52, "feature_discovery": 47, "onboarding": 30,
            "performance": 29, "device_connection": 28,
        })
        self.assertEqual(dict(sentiments), {"positive": 52, "negative": 85, "neutral": 49})

    def test_invalid_inputs_are_not_semantic_unknown(self):
        for value in (None, 0, False, [], {}, "", " \t\n\u3000"):
            with self.subTest(value=value):
                result = classify_feedback_text(value)
                reason = "empty_text" if isinstance(value, str) else "non_string_input"
                self.assertEqual(result["classification_status"], "invalid_input")
                self.assertEqual(result["category_unknown_reason"], reason)
                self.assertEqual(result["sentiment_unknown_reason"], reason)
                self.assertEqual(result["matched_rules"], [])
                self.assertFalse(result["is_ambiguous"])

    def test_nonempty_unknown_is_valid_input(self):
        for text in ("None", "今天天气怎么样？", "xyz"):
            result = classify_feedback_text(text)
            self.assertEqual(result["classification_status"], "unknown")
            self.assertEqual(result["category_unknown_reason"], "no_supported_topic")
            self.assertEqual(result["sentiment_unknown_reason"], "no_supported_sentiment")

    def test_independent_topics_not_hidden_by_priority(self):
        result = classify_feedback_text("设备连接失败，而且页面加载很慢。")
        self.assertEqual(result["category_candidates"], ["device_connection", "performance"])
        self.assertEqual(result["category_unknown_reason"], "multiple_independent_topics")
        self.assertEqual(result["classification_status"], "partially_classified")
        self.assertTrue(result["conflict_reasons"])
        self.assertFalse(any(m["selected"] for m in result["matched_rules"] if m["dimension"] == "category"))

    def test_same_topic_multiple_problems_are_compatible(self):
        self.assert_labels("页面加载慢，操作响应时间也较长。", "performance", "negative")

    def test_setup_guidance_has_no_invented_onboarding_candidate(self):
        result = classify_feedback_text(ORIGINALS[11][0])
        self.assertEqual(result["category_candidates"], ["device_connection"])
        self.assertEqual(result["category_rule"], "C.OVERLAP.SETUP_GUIDANCE")

    def test_guidance_word_alone_does_not_imply_onboarding(self):
        result = classify_feedback_text("请提供指引。")
        self.assertEqual(result["category"], "unknown")
        self.assertEqual(result["category_candidates"], [])

    def test_compatible_rules_have_separate_evidence_not_ambiguity(self):
        text = "建议增加一个功能介绍入口。"
        result = classify_feedback_text(text)
        matches = {m["rule_id"]: m for m in result["matched_rules"] if m["dimension"] == "category"}
        self.assertTrue(matches["C.FEATURE.ENTRY"]["selected"])
        self.assertFalse(matches["C.FEATURE.DESCRIPTION"]["selected"])
        self.assertEqual(result["ambiguity_reasons"], [])
        self.assertEqual(result["conflict_reasons"], [])

    def test_evidence_spans_always_reference_original_text(self):
        texts = [row[0] for row in ORIGINALS] + [row[0] for row in EXTRA_CASES]
        texts += ["  😀设备\u3000连接很稳定。\n", "ﬃ页面加载很慢。", "设备连接失败；设备连接失败。"]
        for text in texts:
            with self.subTest(text=text):
                result = classify_feedback_text(text)
                for evidence in result["matched_rules"]:
                    self.assertEqual(text[evidence["start"]:evidence["end"]], evidence["matched_text"])
                    self.assertLess(evidence["start"], evidence["end"])
                    self.assertIn(evidence["match_kind"], ("specific_phrase", "keyword_combination"))

    def test_normalization_preserves_original_and_expansion_mapping(self):
        text = "  Ａ\u3000\tﬃ  设备连接不好用。\n"
        document = normalize_text(text)
        self.assertEqual(document.original, text)
        self.assertEqual(document.text, "A ffi 设备连接不好用。")
        start = document.text.index("ffi")
        left, right = document.original_span(start, start + 3)
        self.assertEqual(text[left:right], "ﬃ")
        self.assertIn("不", document.text)

    def test_all_occurrences_retained_and_no_duplicates(self):
        result = classify_feedback_text("设备连接失败；设备连接失败。")
        evidence = [m for m in result["matched_rules"] if m["rule_id"] == "C.DEVICE.CONNECTION"]
        self.assertEqual(len(evidence), 2)
        self.assertTrue(all(m["selected"] for m in evidence))
        keys = [(m["rule_id"], m["start"], m["end"], m["match_kind"]) for m in result["matched_rules"]]
        self.assertEqual(len(keys), len(set(keys)))

    def test_unknown_conflict_preserves_unselected_sentiment_evidence(self):
        result = classify_feedback_text("设备连接很稳定，但是经常断线。")
        self.assertEqual(result["sentiment_unknown_reason"], "conflicting_sentiment")
        self.assertEqual(result["sentiment_candidates"], ["positive", "negative"])
        self.assertFalse(any(m["selected"] for m in result["matched_rules"] if m["dimension"] == "sentiment"))

    def test_praise_and_suggestion_not_a_conflict(self):
        self.assert_labels("设备连接很稳定，建议增加操作说明。", "device_connection", "positive")

    def test_complex_context_is_preserved_as_unknown(self):
        result = classify_feedback_text("设备连接不是不稳定。")
        self.assertEqual(result["sentiment_unknown_reason"], "unsupported_context")
        self.assertTrue(any(m["selected"] and m["rule_id"] == "S.UNKNOWN.CONTEXT" for m in result["matched_rules"]))

    def test_general_experience_is_not_always_positive(self):
        self.assert_labels("整体体验很差。", "general_experience", "negative")

    def test_explicit_negated_praise_is_negative(self):
        self.assert_labels("设备连接很不稳定。", "device_connection", "negative")
        self.assert_labels("整体体验不方便也不好用。", "general_experience", "negative")

    def test_negated_failure_is_not_misreported_as_negative(self):
        result = classify_feedback_text("设备连接没有掉线。")
        self.assertEqual(result["sentiment"], "unknown")
        self.assertEqual(result["sentiment_unknown_reason"], "unsupported_context")

    def test_multiple_object_names_do_not_prove_independent_problems(self):
        result = classify_feedback_text("设备连接、页面加载。")
        self.assertEqual(result["category"], "unknown")
        self.assertEqual(result["category_unknown_reason"], "ambiguous_topic")

    def test_result_is_json_serializable_and_has_full_contract(self):
        result = classify_feedback_text("建议增加一个功能介绍入口。")
        self.assertEqual(set(result), {
            "category", "sentiment", "category_rule", "sentiment_rule", "matched_keywords",
            "category_candidates", "sentiment_candidates", "matched_rules", "is_ambiguous",
            "ambiguity_reasons", "conflict_reasons", "classification_status",
            "category_unknown_reason", "sentiment_unknown_reason", "rule_version",
        })
        self.assertEqual(json.loads(json.dumps(result, ensure_ascii=False)), result)
        self.assertEqual(result["rule_version"], "1.0.0")
        self.assertEqual(RULE_VERSION, "1.0.0")

    def test_rules_have_unique_ids_and_explicit_match_kinds(self):
        rules = CATEGORY_RULES + SENTIMENT_RULES
        self.assertEqual(len(rules), len({r.rule_id for r in rules}))
        self.assertTrue(all(r.rule_id.startswith("C.") for r in CATEGORY_RULES))
        self.assertTrue(all(r.rule_id.startswith("S.") for r in SENTIMENT_RULES))
        self.assertTrue(all(p.match_kind in ("specific_phrase", "keyword_combination") for r in rules for p in r.patterns))

    def test_result_independent_of_rule_iteration_order(self):
        samples = [row[0] for row in ORIGINALS] + [row[0] for row in BOUNDARY_CASES]
        expected = [classify_feedback_text(text) for text in samples]
        with patch("src.data.feedback_classifier.category_classifier.CATEGORY_RULES", tuple(reversed(CATEGORY_RULES))):
            with patch("src.data.feedback_classifier.sentiment_classifier.SENTIMENT_RULES", tuple(reversed(SENTIMENT_RULES))):
                self.assertEqual([classify_feedback_text(text) for text in samples], expected)

    def test_each_call_returns_fresh_mutable_result(self):
        text = "设备连接很稳定。"
        first = classify_feedback_text(text)
        expected = classify_feedback_text(text)
        first["matched_rules"].clear()
        first["category_candidates"].append("performance")
        self.assertEqual(classify_feedback_text(text), expected)


if __name__ == "__main__":
    unittest.main()
