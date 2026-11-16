import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import HttpUrl, SecretStr, ValidationError

from nexusguard.infrastructure.llm import ChatMessage, LLMError, ModelGateway, ModelTier


@pytest.fixture
def llm_settings(settings):
    return settings.model_copy(
        update={
            "llm_base_url": HttpUrl("https://model.example/compatible-mode/v1"),
            "llm_api_key": SecretStr("unit-test-key"),
        }
    )


def response_payload(content="OK", finish_reason="stop"):
    return {
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 8, "completion_tokens": 1},
    }


@pytest.mark.parametrize("tier", list(ModelTier))
def test_model_routing_and_request_contract(llm_settings, tier):
    def handler(request):
        body = json.loads(request.content)
        expected = (
            llm_settings.llm_fast_model
            if tier == ModelTier.FAST
            else llm_settings.llm_reasoning_model
        )
        assert str(request.url) == "https://model.example/compatible-mode/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer unit-test-key"
        assert body["model"] == expected
        assert body["max_tokens"] == 64
        assert body["stream"] is False
        assert "tools" not in body
        return httpx.Response(200, json=response_payload())

    async def run():
        gateway = ModelGateway(llm_settings, transport=httpx.MockTransport(handler))
        try:
            result = await gateway.complete(
                tier, [ChatMessage(role="user", content="test")], max_output_tokens=64
            )
            assert result.content == "OK"
            assert result.input_tokens == 8
            assert result.output_tokens == 1
        finally:
            await gateway.aclose()

    asyncio.run(run())


def test_upstream_error_is_sanitized_and_not_retried(llm_settings):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(429, text="unit-test-key private provider details")

    async def run():
        gateway = ModelGateway(llm_settings, transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(LLMError) as caught:
                await gateway.complete(ModelTier.FAST, [ChatMessage(role="user", content="test")])
            assert caught.value.code == "llm_upstream_error"
            assert caught.value.status_code == 429
            assert "unit-test-key" not in str(caught.value)
            assert "private" not in str(caught.value)
            assert calls == 1
        finally:
            await gateway.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "payload",
    [
        {"choices": []},
        response_payload(content=""),
        response_payload(finish_reason="length"),
        {"choices": [{"message": {"content": "OK", "tool_calls": [{}]}, "finish_reason": "stop"}]},
    ],
)
def test_invalid_or_unexpected_responses_fail_closed(llm_settings, payload):
    async def run():
        gateway = ModelGateway(
            llm_settings,
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
        )
        try:
            with pytest.raises(LLMError):
                await gateway.complete(ModelTier.FAST, [ChatMessage(role="user", content="test")])
        finally:
            await gateway.aclose()

    asyncio.run(run())


def test_timeout_is_sanitized(llm_settings):
    def handler(request):
        raise httpx.ReadTimeout("unit-test-key", request=request)

    async def run():
        gateway = ModelGateway(llm_settings, transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(LLMError, match="^llm_timeout$"):
                await gateway.complete(ModelTier.FAST, [ChatMessage(role="user", content="test")])
        finally:
            await gateway.aclose()

    asyncio.run(run())


def test_concurrency_is_bounded(llm_settings):
    active = 0
    peak = 0

    async def handler(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.001)
            return httpx.Response(200, json=response_payload())
        finally:
            active -= 1

    async def run():
        gateway = ModelGateway(
            llm_settings.model_copy(update={"llm_max_concurrency": 2}),
            transport=httpx.MockTransport(handler),
        )
        try:
            await asyncio.gather(
                *(
                    gateway.complete(ModelTier.FAST, [ChatMessage(role="user", content="test")])
                    for _ in range(8)
                )
            )
        finally:
            await gateway.aclose()
        assert peak == 2
        assert active == 0

    asyncio.run(run())


def test_missing_key_is_not_silently_accepted(settings):
    with pytest.raises(LLMError, match="^llm_not_configured$"):
        ModelGateway(settings)


def test_secret_hidden_in_settings_repr(llm_settings):
    assert "unit-test-key" not in repr(llm_settings)
    assert "unit-test-key" not in repr(llm_settings.llm_api_key)


def test_llm_config_rejects_unsafe_url(llm_settings):
    values = llm_settings.model_dump()
    values["llm_base_url"] = "http://model.example/v1"
    with pytest.raises(ValidationError):
        type(llm_settings)(_env_file=None, **values)


def test_app_closes_gateway_without_calling_models(llm_settings, monkeypatch):
    from fastapi.testclient import TestClient

    from nexusguard import main

    gateway = AsyncMock()
    monkeypatch.setattr(main, "ModelGateway", lambda _: gateway)
    with TestClient(main.create_app(llm_settings)) as client:
        assert client.get("/health/live").status_code == 200
        gateway.complete.assert_not_called()
    gateway.aclose.assert_awaited_once()
