"""Offline tests for DeepSeek and mock intent model providers."""
from __future__ import annotations

import json
import logging
import socket
from types import SimpleNamespace
from unittest import mock
import unittest

import httpx
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    InternalServerError,
    RateLimitError,
)

from src.agent.intent.deepseek_provider import DeepSeekProvider
from src.agent.intent.mock_provider import MockIntentProvider
from src.agent.intent.models import (
    IntentCandidate,
    IntentDraft,
    IntentStatus,
    ProviderResponse,
)
from src.agent.intent.provider import (
    IntentModelProvider,
    IntentProviderError,
    ProviderErrorCode,
)


TEST_API_KEY = "test-only-deepseek-key-do-not-use"


def _environment(**updates):
    values = {
        "DEEPSEEK_API_KEY": TEST_API_KEY,
        "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
        "DEEPSEEK_MODEL": "deepseek-flash",
        "INTENT_MODEL_TIMEOUT": "12",
        "INTENT_MODEL_MAX_RETRIES": "1",
        "INTENT_MAX_OUTPUT_TOKENS": "512",
    }
    values.update(updates)
    return values


def _response(output=None, *, content=None, finish_reason="stop", model="deepseek-flash"):
    if content is None:
        content = json.dumps(output or {"intent_candidates": ["activity_analysis"]})
    return SimpleNamespace(
        model=model,
        choices=[SimpleNamespace(
            finish_reason=finish_reason,
            message=SimpleNamespace(content=content),
        )],
    )


class FakeCompletions:
    def __init__(self, effects):
        self.effects = list(effects)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        effect = self.effects.pop(0) if len(self.effects) > 1 else self.effects[0]
        if isinstance(effect, BaseException):
            raise effect
        return effect


class FakeClientFactory:
    def __init__(self, effects):
        self.completions = FakeCompletions(effects)
        self.client = SimpleNamespace(
            chat=SimpleNamespace(completions=self.completions)
        )
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.client


def _http_error(error_type, status_code):
    request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    response = httpx.Response(status_code, request=request)
    return error_type("simulated SDK failure", response=response, body=None)


class IntentModelsTests(unittest.TestCase):
    def test_statuses_candidates_and_draft_are_json_ready(self):
        draft = IntentDraft(
            intent_candidates=(IntentCandidate.ACTIVITY_ANALYSIS,),
            arguments={"start_date": "2026-11-13"},
            argument_evidence={"start_date": "2026年11月13日"},
            date_expression="2026年11月13日",
            confidence=0.95,
            reason="用户明确查询活跃指标",
        )
        payload = draft.to_dict()
        self.assertEqual(payload["intent_candidates"], ["activity_analysis"])
        self.assertEqual(IntentStatus.READY.value, "ready")
        json.dumps(payload, ensure_ascii=False)


class DeepSeekProviderTests(unittest.TestCase):
    def test_provider_interface_and_request_contract(self):
        self.assertTrue(issubclass(DeepSeekProvider, IntentModelProvider))
        factory = FakeClientFactory([_response(
            {"answer": "ok"}, model="DeepSeek-V4.1-Flash-1017"
        )])
        provider = DeepSeekProvider(
            environ=_environment(), client_factory=factory
        )
        with mock.patch.object(
            socket, "create_connection", side_effect=AssertionError("network forbidden")
        ):
            result = provider.generate_intent("系统提示", "分析活跃")

        self.assertEqual(result["output"], {"answer": "ok"})
        self.assertEqual(result["model"], "DeepSeek-V4.1-Flash-1017")
        self.assertEqual(result["finish_reason"], "stop")
        self.assertEqual(len(factory.calls), 1)
        client_args = factory.calls[0]
        self.assertEqual(client_args["base_url"], "https://api.deepseek.com")
        self.assertEqual(client_args["timeout"], 12.0)
        self.assertEqual(client_args["max_retries"], 0)

        request = factory.completions.calls[0]
        self.assertEqual(request["model"], "deepseek-flash")
        self.assertEqual(request["response_format"], {"type": "json_object"})
        self.assertEqual(request["extra_body"], {"thinking": {"type": "disabled"}})
        self.assertEqual(request["max_tokens"], 512)
        self.assertFalse(request["stream"])
        self.assertNotIn("tools", request)
        self.assertIn("valid JSON object", request["messages"][0]["content"])

    def test_missing_api_key(self):
        values = _environment(DEEPSEEK_API_KEY="")
        with self.assertRaises(IntentProviderError) as raised:
            DeepSeekProvider(environ=values, client_factory=mock.Mock())
        self.assertEqual(raised.exception.code, ProviderErrorCode.MISSING_API_KEY)
        self.assertFalse(raised.exception.retryable)

    def test_missing_model_configuration(self):
        values = _environment(DEEPSEEK_MODEL="")
        with self.assertRaises(IntentProviderError) as raised:
            DeepSeekProvider(environ=values, client_factory=mock.Mock())
        self.assertEqual(raised.exception.code, ProviderErrorCode.MISSING_MODEL)

    def test_invalid_live_configuration_is_rejected(self):
        cases = (
            {"DEEPSEEK_MODEL": "gpt-4"},
            {"DEEPSEEK_BASE_URL": "https://api.openai.com"},
            {"INTENT_MODEL_TIMEOUT": "0"},
            {"INTENT_MODEL_TIMEOUT": "nan"},
            {"INTENT_MODEL_MAX_RETRIES": "-1"},
            {"INTENT_MODEL_MAX_RETRIES": "11"},
            {"INTENT_MAX_OUTPUT_TOKENS": "not-an-integer"},
            {"INTENT_MAX_OUTPUT_TOKENS": "393217"},
        )
        for updates in cases:
            with self.subTest(updates=updates):
                with self.assertRaises(IntentProviderError) as raised:
                    DeepSeekProvider(
                        environ=_environment(**updates),
                        client_factory=mock.Mock(),
                    )
                self.assertEqual(
                    raised.exception.code,
                    ProviderErrorCode.INVALID_CONFIGURATION,
                )

    def test_empty_content_and_missing_choices(self):
        empty_content = _response(content="   ")
        no_choices = SimpleNamespace(model="deepseek-flash", choices=[])
        for response in (empty_content, no_choices):
            with self.subTest(response=response):
                factory = FakeClientFactory([response])
                provider = DeepSeekProvider(
                    environ=_environment(INTENT_MODEL_MAX_RETRIES="0"),
                    client_factory=factory,
                )
                with self.assertRaises(IntentProviderError) as raised:
                    provider.generate_intent("prompt", "input")
                self.assertEqual(raised.exception.code, ProviderErrorCode.EMPTY_RESPONSE)
                self.assertEqual(len(factory.completions.calls), 1)

    def test_invalid_json_and_non_object_json(self):
        for content in ("not-json", "[1, 2, 3]"):
            with self.subTest(content=content):
                factory = FakeClientFactory([_response(content=content)])
                provider = DeepSeekProvider(
                    environ=_environment(), client_factory=factory
                )
                with self.assertRaises(IntentProviderError) as raised:
                    provider.generate_intent("prompt", "input")
                self.assertEqual(raised.exception.code, ProviderErrorCode.INVALID_JSON)
                self.assertEqual(len(factory.completions.calls), 1)

    def test_response_truncated_is_not_retried(self):
        factory = FakeClientFactory([_response(content="{}", finish_reason="length")])
        provider = DeepSeekProvider(
            environ=_environment(INTENT_MODEL_MAX_RETRIES="3"),
            client_factory=factory,
        )
        with self.assertRaises(IntentProviderError) as raised:
            provider.generate_intent("prompt", "input")
        self.assertEqual(raised.exception.code, ProviderErrorCode.RESPONSE_TRUNCATED)
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(len(factory.completions.calls), 1)

    def test_timeout_retries_then_succeeds(self):
        request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
        factory = FakeClientFactory([APITimeoutError(request=request), _response({"ok": True})])
        provider = DeepSeekProvider(
            environ=_environment(INTENT_MODEL_MAX_RETRIES="1"),
            client_factory=factory,
        )
        result = provider.generate_intent("prompt", "input")
        self.assertEqual(result["output"], {"ok": True})
        self.assertEqual(len(factory.completions.calls), 2)

    def test_timeout_is_classified_after_retry_limit(self):
        request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
        factory = FakeClientFactory([APITimeoutError(request=request)])
        provider = DeepSeekProvider(
            environ=_environment(INTENT_MODEL_MAX_RETRIES="1"),
            client_factory=factory,
        )
        with self.assertRaises(IntentProviderError) as raised:
            provider.generate_intent("prompt", "input")
        self.assertEqual(raised.exception.code, ProviderErrorCode.REQUEST_TIMEOUT)
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(len(factory.completions.calls), 2)

    def test_connection_error_is_classified(self):
        request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
        error = APIConnectionError(message="secret transport details", request=request)
        factory = FakeClientFactory([error])
        provider = DeepSeekProvider(
            environ=_environment(INTENT_MODEL_MAX_RETRIES="0"),
            client_factory=factory,
        )
        with self.assertRaises(IntentProviderError) as raised:
            provider.generate_intent("prompt", "input")
        self.assertEqual(raised.exception.code, ProviderErrorCode.CONNECTION_ERROR)
        self.assertTrue(raised.exception.retryable)

    def test_authentication_error_is_not_retried(self):
        factory = FakeClientFactory([_http_error(AuthenticationError, 401)])
        provider = DeepSeekProvider(
            environ=_environment(INTENT_MODEL_MAX_RETRIES="4"),
            client_factory=factory,
        )
        with self.assertRaises(IntentProviderError) as raised:
            provider.generate_intent("prompt", "input")
        self.assertEqual(raised.exception.code, ProviderErrorCode.AUTHENTICATION_ERROR)
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(len(factory.completions.calls), 1)

    def test_rate_limit_is_classified_and_retryable(self):
        factory = FakeClientFactory([_http_error(RateLimitError, 429)])
        provider = DeepSeekProvider(
            environ=_environment(INTENT_MODEL_MAX_RETRIES="0"),
            client_factory=factory,
        )
        with self.assertRaises(IntentProviderError) as raised:
            provider.generate_intent("prompt", "input")
        self.assertEqual(raised.exception.code, ProviderErrorCode.RATE_LIMIT_ERROR)
        self.assertTrue(raised.exception.retryable)

    def test_5xx_is_api_error_and_honors_retry_limit(self):
        factory = FakeClientFactory([_http_error(InternalServerError, 503)])
        provider = DeepSeekProvider(
            environ=_environment(INTENT_MODEL_MAX_RETRIES="2"),
            client_factory=factory,
        )
        with self.assertRaises(IntentProviderError) as raised:
            provider.generate_intent("prompt", "input")
        self.assertEqual(raised.exception.code, ProviderErrorCode.API_ERROR)
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(len(factory.completions.calls), 3)

    def test_key_is_absent_from_errors_repr_and_logs(self):
        factory = FakeClientFactory([_http_error(AuthenticationError, 401)])
        provider = DeepSeekProvider(
            environ=_environment(INTENT_MODEL_MAX_RETRIES="0"),
            client_factory=factory,
        )
        handler = logging.Handler()
        records = []
        handler.emit = records.append  # type: ignore[method-assign]
        root = logging.getLogger()
        root.addHandler(handler)
        try:
            with self.assertRaises(IntentProviderError) as raised:
                provider.generate_intent("prompt", "input")
        finally:
            root.removeHandler(handler)
        visible = f"{raised.exception!s} {raised.exception!r} {raised.exception.to_dict()}"
        visible += " ".join(record.getMessage() for record in records)
        self.assertNotIn(TEST_API_KEY, visible)
        self.assertNotIn("simulated SDK failure", visible)
        self.assertIsNone(raised.exception.__cause__)

    def test_configured_pro_model_never_falls_back_to_flash(self):
        factory = FakeClientFactory([_http_error(InternalServerError, 503)])
        provider = DeepSeekProvider(
            environ=_environment(
                DEEPSEEK_MODEL="deepseek-v4-pro",
                INTENT_MODEL_MAX_RETRIES="1",
            ),
            client_factory=factory,
        )
        with self.assertRaises(IntentProviderError):
            provider.generate_intent("prompt", "input")
        self.assertEqual(len(factory.completions.calls), 2)
        self.assertTrue(all(
            call["model"] == "deepseek-v4-pro"
            for call in factory.completions.calls
        ))


class MockIntentProviderTests(unittest.TestCase):
    def test_mock_provider_interface_repeats_fresh_response_without_network(self):
        self.assertTrue(issubclass(MockIntentProvider, IntentModelProvider))
        response = ProviderResponse(
            output={"intent_candidates": ["retention_analysis"]},
            model="mock-intent-model",
            finish_reason="stop",
        )
        provider = MockIntentProvider(response=response)
        with mock.patch.object(
            socket, "create_connection", side_effect=AssertionError("network forbidden")
        ):
            first = provider.generate_intent("prompt", "input")
            first["output"]["intent_candidates"].append("changed")
            second = provider.generate_intent("prompt", "input")
        self.assertEqual(second["output"]["intent_candidates"], ["retention_analysis"])
        self.assertEqual(provider.call_count, 2)
        self.assertEqual(len(provider.calls), 2)

    def test_mock_response_factory_receives_stable_call_number(self):
        seen = []

        def factory(system_prompt, user_input, call_number):
            seen.append((system_prompt, user_input, call_number))
            return ProviderResponse(
                output={"call": call_number},
                model="mock-intent-model",
                finish_reason="stop",
            )

        provider = MockIntentProvider(response_factory=factory)
        self.assertEqual(provider.generate_intent("p", "u")["output"]["call"], 1)
        self.assertEqual(provider.generate_intent("p", "u")["output"]["call"], 2)
        self.assertEqual(seen, [("p", "u", 1), ("p", "u", 2)])

    def test_mock_provider_can_simulate_api_exception(self):
        error = IntentProviderError(
            ProviderErrorCode.RATE_LIMIT_ERROR,
            "simulated rate limit",
            retryable=True,
        )
        provider = MockIntentProvider(error=error)
        with self.assertRaises(IntentProviderError) as raised:
            provider.generate_intent("prompt", "input")
        self.assertIs(raised.exception, error)
        self.assertEqual(provider.call_count, 1)

    def test_mock_requires_exactly_one_behavior(self):
        with self.assertRaises(ValueError):
            MockIntentProvider()
        with self.assertRaises(ValueError):
            MockIntentProvider(response={}, response_factory=lambda *_: {})


if __name__ == "__main__":
    unittest.main()
