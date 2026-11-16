"""Conservative normalization with reversible original evidence positions."""
from __future__ import annotations

from dataclasses import dataclass
import unicodedata


@dataclass(frozen=True)
class NormalizedText:
    original: str
    text: str
    positions: tuple[tuple[int, int], ...]
    invalid_reason: str | None = None

    def original_span(self, start: int, end: int) -> tuple[int, int]:
        return self.positions[start][0], self.positions[end - 1][1]


def normalize_text(value: object) -> NormalizedText:
    """Normalize character widths and whitespace, preserving negation words.

    Offsets are Python Unicode code-point indices into the original string.
    Each normalized code point maps to an original half-open interval, including
    compatibility characters that expand to several normalized code points.
    """
    if not isinstance(value, str):
        return NormalizedText("", "", (), "non_string_input")
    chars, positions = [], []
    for index, char in enumerate(value):
        for normalized in unicodedata.normalize("NFKC", char):
            if normalized.isspace():
                if chars and chars[-1] == " ":
                    positions[-1] = (positions[-1][0], index + 1)
                    continue
                normalized = " "
            chars.append(normalized)
            positions.append((index, index + 1))
    while chars and chars[-1] == " ":
        chars.pop()
        positions.pop()
    if chars and chars[0] == " ":
        del chars[0]
        del positions[0]
    text = "".join(chars)
    return NormalizedText(value, text, tuple(positions), None if text else "empty_text")
