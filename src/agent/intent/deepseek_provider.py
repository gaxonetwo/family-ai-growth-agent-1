"""DeepSeek Chat Completions provider using the OpenAI-compatible SDK."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import json
import math
import os
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)

from src.agent.intent.models import ProviderResponse
from src.agent.intent.provider import (
    IntentModelProvider,
    IntentProviderError,
    ProviderErrorCode,
)


OFFICIAL_BASE_URL = "https://api.deepseek.com"
ALLOWED_MODELS = frozenset({"deepseek-flash", "deepseek-v4-pro"})
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_RETRIES = 1
DEFAULT_MAX_OUTPUT_TOKENS = 2048
MAX_CONFIGURED_RETRIES = 10
MAX_OUTPUT_TOKENS = 393_216


@dataclass(frozen=True)
class DeepSeekConfig:
    """Validated live configuration loaded only from environment values."""

    api_key: str = field(repr=False, compare=False)
    base_url: str
    model: str
    timeout_seconds: float
    max_retries: int
    max_output_tokens: int

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "DeepSeekConfig":
        values = os.environ if environ is None else environ
        api_key = values.get("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            raise IntentProviderError(
                ProviderErrorCode.MISSING_API_KEY,
                "DEEPSEEK_API_KEY is required for the live DeepSeek provider.",
                retryable=False,
            )

        model = values.get("DEEPSEEK_MODEL", "").strip()
        if not model:
            raise IntentProviderError(
                ProviderErrorCode.MISSING_MODEL,
                "DEEPSEEK_MODEL is required for the live DeepSeek provider.",
                retryable=False,
            )
        if model not in ALLOWED_MODELS:
            raise IntentProviderError(
                ProviderErrorCode.INVALID_CONFIGURATION,
                "DEEPSEEK_MODEL must be an approved DeepSeek model identifier.",
                retryable=False,
            )

        base_url = values.get("DEEPSEEK_BASE_URL", OFFICIAL_BASE_URL).strip().rstrip("/")
        if base_url != OFFICIAL_BASE_URL:
            raise IntentProviderError(
                ProviderErrorCode.INVALID_CONFIGURATION,
                "DEEPSEEK_BASE_URL must point to the official DeepSeek API.",
                retryable=False,
            )

        timeout = _read_float(
            values,
            "INTENT_MODEL_TIMEOUT",
            DEFAULT_TIMEOUT_SECONDS,
            minimum_exclusive=0,
        )
        retries = _read_int(
            values,
            "INTENT_MODEL_MAX_RETRIES",
            DEFAULT_MAX_RETRIES,
            minimum=0,
            maximum=MAX_CONFIGURED_RETRIES,
        )
        output_tokens = _read_int(
            values,
            "INTENT_MAX_OUTPUT_TOKENS",
            DEFAULT_MAX_OUTPUT_TOKENS,
            minimum=1,
            maximum=MAX_OUTPUT_TOKENS,
        )
        return cls(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout_seconds=timeout,
            max_retries=retries,
            max_output_tokens=output_tokens,
        )


def _read_float(
    environ: Mapping[str, str],
    name: str,
    default: float,
    *,
    minimum_exclusive: float,
) -> float:
    raw = environ.get(name, "").strip()
    try:
        value = default if not raw else float(raw)
    except ValueError as exc:
        raise IntentProviderError(
            ProviderErrorCode.INVALID_CONFIGURATION,
            f"{name} must be a number.",
            retryable=False,
        ) from exc
    if not math.isfinite(value) or value <= minimum_exclusive:
        raise IntentProviderError(
            ProviderErrorCode.INVALID_CONFIGURATION,
            f"{name} must be greater than {minimum_exclusive}.",
            retryable=False,
        )
    return value


def _read_int(
    environ: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw = environ.get(name, "").strip()
    try:
        value = default if not raw else int(raw)
    except ValueError as exc:
        raise IntentProviderError(
            ProviderErrorCode.INVALID_CONFIGURATION,
            f"{name} must be an integer.",
            retryable=False,
        ) from exc
    if value < minimum:
        raise IntentProviderError(
            ProviderErrorCode.INVALID_CONFIGURATION,
            f"{name} must be at least {minimum}.",
            retryable=False,
        )
    if value > maximum:
        raise IntentProviderError(
            ProviderErrorCode.INVALID_CONFIGURATION,
            f"{name} must be at most {maximum}.",
            retryable=False,
        )
    return value


ClientFactory = Callable[..., Any]


class DeepSeekProvider(IntentModelProvider):
    """Live DeepSeek provider. Construction alone does not make a request."""

    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self.config = DeepSeekConfig.from_environment(environ)
        factory = client_factory or OpenAI
        # SDK retries are disabled so this class has one observable retry policy.
        self._client = factory(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            timeout=self.config.timeout_seconds,
            max_retries=0,
        )

    def generate_intent(
        self,
        system_prompt: str,
        user_input: str,
    ) -> dict[str, Any]:
        if not isinstance(system_prompt, str) or not system_prompt.strip():
            raise IntentProviderError(
                ProviderErrorCode.INVALID_CONFIGURATION,
                "system_prompt must be a non-empty string.",
                retryable=False,
            )
        if not isinstance(user_input, str) or not user_input.strip():
            raise IntentProviderError(
                ProviderErrorCode.INVALID_CONFIGURATION,
                "user_input must be a non-empty string.",
                retryable=False,
            )

        final_error: IntentProviderError | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                return self._request_once(system_prompt, user_input)
            except Exception as exc:  # Convert SDK errors into the safe contract.
                error = _classify_error(exc)
                final_error = error
                if not error.retryable or attempt >= self.config.max_retries:
                    # Do not expose the SDK exception chain, which may contain
                    # transport or request details not intended for callers.
                    raise error from None
        # The loop always returns or raises.  This guard keeps the type contract explicit.
        assert final_error is not None
        raise final_error

    def _request_once(self, system_prompt: str, user_input: str) -> dict[str, Any]:
        json_instruction = (
            "Return exactly one valid JSON object. Do not use Markdown fences, "
            "comments, or text outside the JSON object."
        )
        response = self._client.chat.completions.create(
            model=self.config.model,
            messages=[
                {
                    "role": "system",
                    "content": f"{system_prompt.rstrip()}\n\n{json_instruction}",
                },
                {"role": "user", "content": user_input},
            ],
            response_format={"type": "json_object"},
            max_tokens=self.config.max_output_tokens,
            stream=False,
            # DeepSeek requires this extension inside extra_body with the OpenAI SDK.
            extra_body={"thinking": {"type": "disabled"}},
        )
        choices = getattr(response, "choices", None)
        if not choices:
            raise IntentProviderError(
                ProviderErrorCode.EMPTY_RESPONSE,
                "DeepSeek returned no completion choices.",
                retryable=False,
            )
        choice = choices[0]
        finish_reason = str(getattr(choice, "finish_reason", "") or "")
        if finish_reason == "length":
            raise IntentProviderError(
                ProviderErrorCode.RESPONSE_TRUNCATED,
                "DeepSeek output was truncated before completion.",
                retryable=False,
            )
        message = getattr(choice, "message", None)
        content = getattr(message, "content", None) if message is not None else None
        if not isinstance(content, str) or not content.strip():
            raise IntentProviderError(
                ProviderErrorCode.EMPTY_RESPONSE,
                "DeepSeek returned an empty response.",
                retryable=False,
            )
        try:
            output = json.loads(content)
        except json.JSONDecodeError as exc:
            raise IntentProviderError(
                ProviderErrorCode.INVALID_JSON,
                "DeepSeek response was not valid JSON.",
                retryable=False,
            ) from exc
        if not isinstance(output, dict):
            raise IntentProviderError(
                ProviderErrorCode.INVALID_JSON,
                "DeepSeek response JSON must be an object.",
                retryable=False,
            )
        response_model = str(getattr(response, "model", "") or self.config.model)
        return ProviderResponse(
            output=output,
            model=response_model,
            finish_reason=finish_reason,
        ).to_dict()


def _classify_error(exc: Exception) -> IntentProviderError:
    if isinstance(exc, IntentProviderError):
        return exc
    if isinstance(exc, AuthenticationError):
        return IntentProviderError(
            ProviderErrorCode.AUTHENTICATION_ERROR,
            "DeepSeek authentication failed. Check the configured API key.",
            retryable=False,
        )
    if isinstance(exc, RateLimitError):
        return IntentProviderError(
            ProviderErrorCode.RATE_LIMIT_ERROR,
            "DeepSeek rate limit was reached.",
            retryable=True,
        )
    if isinstance(exc, APITimeoutError):
        return IntentProviderError(
            ProviderErrorCode.REQUEST_TIMEOUT,
            "DeepSeek request timed out.",
            retryable=True,
        )
    if isinstance(exc, APIConnectionError):
        return IntentProviderError(
            ProviderErrorCode.CONNECTION_ERROR,
            "Could not connect to the DeepSeek API.",
            retryable=True,
        )
    if isinstance(exc, APIStatusError):
        status_code = getattr(exc, "status_code", None)
        return IntentProviderError(
            ProviderErrorCode.API_ERROR,
            "DeepSeek API returned a server error."
            if isinstance(status_code, int) and status_code >= 500
            else "DeepSeek API rejected the request.",
            retryable=isinstance(status_code, int) and status_code >= 500,
        )
    return IntentProviderError(
        ProviderErrorCode.API_ERROR,
        "DeepSeek provider encountered an unexpected API error.",
        retryable=False,
    )
