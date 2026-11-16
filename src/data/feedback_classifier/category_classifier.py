"""Independent topic matching and conservative single-topic selection."""
from __future__ import annotations

import re

from .conflict_resolver import resolve_category
from .normalizer import NormalizedText
from .rules import CATEGORY_RULES, find_matches


def classify_category(document: NormalizedText) -> tuple[dict, list[dict]]:
    matches = find_matches(document, CATEGORY_RULES, "category")
    # Feature hints are an approved overlap only without a first-use/signup
    # context in the same sentence; unrelated onboarding sentences remain.
    for match in matches[:]:
        if match["rule_id"] != "C.OVERLAP.FEATURE_HINT":
            continue
        left = re.split(r"[。！？!?；;]", document.text[:match["normalized_start"]])[-1]
        if re.search(r"注册|首次|第一次|新手", left):
            matches.remove(match)
    return resolve_category(matches, document.text), matches
