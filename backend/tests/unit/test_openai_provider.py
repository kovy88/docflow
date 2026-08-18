"""OpenAIProvider's translation of `LLMRequest` into an SDK call.

The vendor SDK boundary is the one place this codebase touches OpenAI's API
shape directly, so it's the only place a per-model quirk like the
temperature restriction below can be tested without a real network call —
mocking `chat.completions.create` here tests OpenAIProvider's own logic, not
a collaborator's business logic (see conftest.py's docstring on why the
pipeline-level tests use the real FixtureProvider instead of a mock).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from docflow.llm.base import LLMRequest
from docflow.llm.openai_provider import OpenAIProvider

REQUEST = LLMRequest(
    system="You are an extraction engine.",
    user_content="Invoice text goes here.",
    json_schema={
        "type": "object",
        "properties": {"total": {"type": "string"}},
        "required": ["total"],
    },
    schema_name="test_schema",
)


def _fake_completion(model: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content='{"total": "100.00"}', refusal=None),
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=42,
            completion_tokens=7,
            prompt_tokens_details=SimpleNamespace(cached_tokens=0),
        ),
        model=model,
    )


async def _call_and_capture(model: str) -> dict[str, Any]:
    """Build a provider for `model`, fire one request, return the kwargs the
    OpenAI SDK's `chat.completions.create` was actually called with."""
    provider = OpenAIProvider(api_key="sk-test", model=model)
    mock_create = AsyncMock(return_value=_fake_completion(model))
    provider._client.chat.completions.create = mock_create

    response = await provider.complete_structured(REQUEST)

    assert response.data == {"total": "100.00"}
    _, kwargs = mock_create.call_args
    return kwargs


class TestTemperatureCompatibility:
    """GPT-5.6 rejects any non-default `temperature` with a hard 400
    (`Unsupported value: 'temperature' does not support 0 with this model.
    Only the default (1) value is supported.`) — confirmed against the real
    API with gpt-5.6-luna, not inferred from docs. See the comment on
    `OpenAIProvider._NO_TEMPERATURE_PREFIXES`. Every other OpenAI model this
    codebase talks to still expects the parameter, so the fix has to be
    conditional on the model rather than a blanket drop.
    """

    @pytest.mark.parametrize("model", ["gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra"])
    async def test_gpt_5_6_family_omits_temperature(self, model: str) -> None:
        kwargs = await _call_and_capture(model)
        assert "temperature" not in kwargs

    @pytest.mark.parametrize("model", ["gpt-4.1", "gpt-4.1-mini"])
    async def test_gpt_4_1_family_still_sends_temperature(self, model: str) -> None:
        kwargs = await _call_and_capture(model)
        assert kwargs["temperature"] == REQUEST.temperature
