"""Tests for stable, lossless tool execution envelopes."""
from __future__ import annotations

import json
import math
import unittest

from src.agent.result_formatter import (
    ResultSerializationError,
    format_failure,
    format_success,
)


class ResultFormatterTests(unittest.TestCase):
    def test_success_shape_and_original_metrics_are_preserved(self):
        data = {"activation_rate": 0.5, "users": 8}
        result = format_success("activation", data)
        self.assertTrue(result["ok"])
        self.assertIs(result["data"], data)
        self.assertEqual(result["data"]["activation_rate"], 0.5)
        self.assertIsNone(result["error"])
        self.assertEqual(result["metadata"]["execution_status"], "success")

    def test_failure_shape_is_never_marked_successful(self):
        result = format_failure("activity", "bad_input", "invalid date")
        self.assertFalse(result["ok"])
        self.assertIsNone(result["data"])
        self.assertEqual(result["error"]["code"], "bad_input")
        self.assertEqual(result["metadata"]["execution_status"], "failed")

    def test_none_and_unknown_values_are_preserved(self):
        data = {"mau": None, "sentiment": "unknown"}
        result = format_success("sample", data)
        self.assertIsNone(result["data"]["mau"])
        self.assertEqual(result["data"]["sentiment"], "unknown")

    def test_success_and_failure_are_json_serializable(self):
        success = format_success("sample", {"items": [1, True, None]})
        failure = format_failure("sample", "failed", "原因", details={"field": "x"})
        json.dumps(success, ensure_ascii=False)
        json.dumps(failure, ensure_ascii=False)

    def test_unsupported_object_raises_explicit_error(self):
        with self.assertRaisesRegex(ResultSerializationError, "not JSON-serializable"):
            format_success("sample", {"value": object()})

    def test_non_finite_float_is_rejected(self):
        with self.assertRaises(ResultSerializationError):
            format_success("sample", {"value": math.nan})

    def test_metadata_cannot_override_execution_status(self):
        success = format_success("sample", {}, metadata={"execution_status": "failed"})
        failure = format_failure(
            "sample", "x", "x", metadata={"execution_status": "success"}
        )
        self.assertEqual(success["metadata"]["execution_status"], "success")
        self.assertEqual(failure["metadata"]["execution_status"], "failed")


if __name__ == "__main__":
    unittest.main()
