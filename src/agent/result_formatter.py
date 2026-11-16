"""Stable execution envelopes for Agent tool calls."""
from __future__ import annotations

import json
from typing import Any


class ResultSerializationError(TypeError):
    """Raised when a tool result cannot be represented as strict JSON."""


def _ensure_json_serializable(value: Any, label: str) -> None:
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ResultSerializationError(
            f"{label} is not JSON-serializable: {exc}"
        ) from exc


def format_success(
    tool_name: str,
    data: Any,
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Wrap a successful result without rewriting its business values."""
    envelope = {
        "ok": True,
        "tool_name": tool_name,
        "data": data,
        "error": None,
        "metadata": {**(metadata or {}), "execution_status": "success"},
    }
    _ensure_json_serializable(envelope, "Tool success result")
    return envelope


def format_failure(
    tool_name: str,
    code: str,
    message: str,
    *,
    details: Any = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a failed envelope; optional details retain safe error evidence."""
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    envelope = {
        "ok": False,
        "tool_name": tool_name,
        "data": None,
        "error": error,
        "metadata": {**(metadata or {}), "execution_status": "failed"},
    }
    _ensure_json_serializable(envelope, "Tool failure result")
    return envelope
