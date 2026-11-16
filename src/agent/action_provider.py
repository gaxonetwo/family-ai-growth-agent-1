"""Offline action responses reuse the existing injected Mock provider."""
from src.agent.intent.mock_provider import MockIntentProvider


class MockActionProvider(MockIntentProvider):
    """Same response/factory/error injection; no intent or keyword classifier."""

    def generate_action(self, system_prompt: str, user_input: str) -> dict:
        return self.generate_intent(system_prompt, user_input)
