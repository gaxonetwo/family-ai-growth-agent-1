"""Provider abstraction and safe provider error contract."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from src.agent.intent.models import IntentError


class ProviderErrorCode:
    """Stable provider error codes exposed to callers and tests."""

    MISSING_API_KEY = "missing_api_key"
    MISSING_MODEL = "missing_model"
    INVALID_CONFIGURATION = "invalid_configuration"
    AUTHENTICATION_ERROR = "authentication_error"
    RATE_LIMIT_ERROR = "rate_limit_error"
    REQUEST_TIMEOUT = "request_timeout"
    CONNECTION_ERROR = "connection_error"
    EMPTY_RESPONSE = "empty_response"
    INVALID_JSON = "invalid_json"
    RESPONSE_TRUNCATED = "response_truncated"
    API_ERROR = "api_error"


class IntentProviderError(RuntimeError):
    """A model-provider failure with a safe code and retry classification."""

    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        self.error = IntentError(code=code, message=message, retryable=retryable)
        super().__init__(message)

    @property
    def code(self) -> str:
        return self.error.code

    @property
    def retryable(self) -> bool:
        return self.error.retryable

    def to_dict(self) -> dict[str, str | bool]:
        return self.error.to_dict()


class IntentModelProvider(ABC):
    """Transport-independent interface used by a future IntentParser."""

    @abstractmethod
    def generate_intent(
        self,
        system_prompt: str,
        user_input: str,
    ) -> dict[str, Any]:
        """Return ``output``, ``model`` and ``finish_reason`` metadata."""
