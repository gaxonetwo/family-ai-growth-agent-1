"""Deterministic offline provider for tests and explicit demonstrations."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any

from src.agent.intent.models import ProviderResponse
from src.agent.intent.provider import IntentModelProvider, IntentProviderError


MockResponse = ProviderResponse | Mapping[str, Any]
MockResponseFactory = Callable[[str, str, int], MockResponse]


class MockIntentProvider(IntentModelProvider):
    """Return injected data or errors without importing or opening a network client."""

    def __init__(
        self,
        *,
        response: MockResponse | None = None,
        response_factory: MockResponseFactory | None = None,
        error: IntentProviderError | None = None,
    ) -> None:
        configured = sum(item is not None for item in (response, response_factory, error))
        if configured != 1:
            raise ValueError(
                "Configure exactly one of response, response_factory, or error."
            )
        self._response = response
        self._response_factory = response_factory
        self._error = error
        self._calls: list[dict[str, str]] = []

    @property
    def call_count(self) -> int:
        return len(self._calls)

    @property
    def calls(self) -> tuple[dict[str, str], ...]:
        return tuple(deepcopy(self._calls))

    def generate_intent(
        self,
        system_prompt: str,
        user_input: str,
    ) -> dict[str, Any]:
        self._calls.append({"system_prompt": system_prompt, "user_input": user_input})
        if self._error is not None:
            raise self._error
        value = (
            self._response_factory(system_prompt, user_input, self.call_count)
            if self._response_factory is not None
            else self._response
        )
        if isinstance(value, ProviderResponse):
            return value.to_dict()
        if not isinstance(value, Mapping):
            raise TypeError("Mock response must be a ProviderResponse or mapping.")
        return deepcopy(dict(value))
