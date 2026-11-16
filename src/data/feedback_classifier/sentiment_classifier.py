"""Sentiment is inferred from expressions, never from the topic label."""
from __future__ import annotations

import re

from .conflict_resolver import resolve_sentiment
from .normalizer import NormalizedText
from .rules import SENTIMENT_RULES, find_matches


def classify_sentiment(document: NormalizedText) -> tuple[dict, list[dict]]:
    matches = find_matches(document, SENTIMENT_RULES, "sentiment")
    for match in matches[:]:
        if match["label"] != "positive":
            continue
        prefix = re.split(r"[。！？!?；;，,]", document.text[:match["normalized_start"]])[-1]
        # Desired improvements are not praise; negated praise is not positive.
        if re.search(r"希望|建议|需要|请", prefix) or prefix.endswith(("不", "没", "无")):
            matches.remove(match)
    return resolve_sentiment(matches), matches
