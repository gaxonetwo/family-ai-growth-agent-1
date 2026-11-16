"""Public pure-text API for explainable local feedback classification."""
from __future__ import annotations

from .category_classifier import classify_category
from .normalizer import normalize_text
from .rules import RULE_VERSION
from .sentiment_classifier import classify_sentiment

__all__ = ["classify_feedback_text"]


def classify_feedback_text(text: object) -> dict:
    """Return independent topic/sentiment decisions with original evidence.

    This function performs no I/O, database access, network calls or mutations
    outside its newly allocated result. Invalid input has a structured result.
    """
    document = normalize_text(text)
    if document.invalid_reason:
        suffix = "EMPTY" if document.invalid_reason == "empty_text" else "TYPE"
        category = {"label": "unknown", "rule": "C.INVALID_INPUT." + suffix,
                    "reason": document.invalid_reason, "candidates": [],
                    "ambiguity_reasons": [], "conflict_reasons": []}
        sentiment = {**category, "rule": "S.INVALID_INPUT." + suffix}
        evidence = []
        status = "invalid_input"
    else:
        category, category_matches = classify_category(document)
        sentiment, sentiment_matches = classify_sentiment(document)
        evidence = category_matches + sentiment_matches
        unknowns = (category["label"] == "unknown") + (sentiment["label"] == "unknown")
        status = ("classified", "partially_classified", "unknown")[unknowns]

    evidence.sort(key=lambda m: (
        0 if m["dimension"] == "category" else 1, m["start"],
        -(m["end"] - m["start"]), m["rule_id"], m["match_kind"],
    ))
    matched_rules = []
    keywords = {"category": [], "sentiment": []}
    decisions = {"category": category, "sentiment": sentiment}
    for match in evidence:
        matched_rules.append({
            key: match[key] for key in (
                "dimension", "rule_id", "match_kind", "matched_text", "start", "end"
            )
        })
        matched_rules[-1]["selected"] = match["rule_id"] == decisions[match["dimension"]]["rule"]
        if match["matched_text"] not in keywords[match["dimension"]]:
            keywords[match["dimension"]].append(match["matched_text"])
    ambiguity = category["ambiguity_reasons"] + sentiment["ambiguity_reasons"]
    conflicts = category["conflict_reasons"] + sentiment["conflict_reasons"]
    return {
        "category": category["label"], "sentiment": sentiment["label"],
        "category_rule": category["rule"], "sentiment_rule": sentiment["rule"],
        "matched_keywords": keywords,
        "category_candidates": category["candidates"],
        "sentiment_candidates": sentiment["candidates"],
        "matched_rules": matched_rules,
        "is_ambiguous": bool(ambiguity or conflicts),
        "ambiguity_reasons": ambiguity, "conflict_reasons": conflicts,
        "classification_status": status,
        "category_unknown_reason": category["reason"],
        "sentiment_unknown_reason": sentiment["reason"],
        "rule_version": RULE_VERSION,
    }
