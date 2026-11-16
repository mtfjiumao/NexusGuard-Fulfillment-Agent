"""Bounded asynchronous model gateway; no business tools or execution authority."""

import asyncio
from enum import StrEnum
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from nexusguard.core.config import Settings


class ModelTier(StrEnum):
    FAST = "fast"
    REASONING = "reasoning"


class ChatMessage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=100_000)


class CompletionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: str
    content: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class LLMError(RuntimeError):
    """Safe error metadata only: never include provider bodies, headers or prompts."""

    def __init__(self, code: str, *, status_code: int | None = None) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


class _ProviderMessage(BaseModel):
    content: str = Field(min_length=1)
    tool_calls: list | None = None


class _ProviderChoice(BaseModel):
    message: _ProviderMessage
    finish_reason: str


class _ProviderUsage(BaseModel):
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)


class _ProviderResponse(BaseModel):
    choices: list[_ProviderChoice] = Field(min_length=1, max_length=1)
    usage: _ProviderUsage | None = None


class ModelGateway:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if (
            settings.llm_base_url is None
            or settings.llm_api_key is None
            or not settings.llm_api_key.get_secret_value().strip()
        ):
            raise LLMError("llm_not_configured")
        self._settings = settings
        self._models = {
            ModelTier.FAST: settings.llm_fast_model,
            ModelTier.REASONING: settings.llm_reasoning_model,
        }
        self._semaphore = asyncio.Semaphore(settings.llm_max_concurrency)
        self._client = httpx.AsyncClient(
            base_url=str(settings.llm_base_url).rstrip("/") + "/",
            headers={"Authorization": "Bearer " + settings.llm_api_key.get_secret_value()},
            timeout=settings.llm_timeout_seconds,
            follow_redirects=False,
            limits=httpx.Limits(
                max_connections=settings.llm_max_concurrency,
                max_keepalive_connections=settings.llm_max_concurrency,
            ),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def complete(
        self,
        tier: ModelTier,
        messages: list[ChatMessage],
        *,
        max_output_tokens: int | None = None,
    ) -> CompletionResult:
        if not isinstance(tier, ModelTier):
            raise ValueError("tier must be a ModelTier")
        if not messages or len(messages) > 64:
            raise ValueError("messages must contain between 1 and 64 entries")
        budget = (
            max_output_tokens
            if max_output_tokens is not None
            else (self._settings.llm_max_output_tokens)
        )
        if (
            isinstance(budget, bool)
            or not isinstance(budget, int)
            or not (1 <= budget <= self._settings.llm_max_output_tokens)
        ):
            raise ValueError("max_output_tokens exceeds the configured budget")
        model = self._models[tier]
        try:
            # The total deadline includes queueing; there are no automatic paid retries.
            async with asyncio.timeout(self._settings.llm_timeout_seconds):
                async with self._semaphore:
                    response = await self._client.post(
                        "chat/completions",
                        json={
                            "model": model,
                            "messages": [message.model_dump() for message in messages],
                            "max_tokens": budget,
                            "stream": False,
                        },
                    )
        except (TimeoutError, httpx.TimeoutException):
            raise LLMError("llm_timeout") from None
        except httpx.RequestError:
            raise LLMError("llm_transport_error") from None

        if response.status_code != 200:
            raise LLMError("llm_upstream_error", status_code=response.status_code)
        try:
            payload = _ProviderResponse.model_validate_json(response.content)
        except ValidationError:
            raise LLMError("llm_invalid_response") from None
        choice = payload.choices[0]
        if choice.finish_reason != "stop" or choice.message.tool_calls:
            raise LLMError("llm_incomplete_or_unsupported_response")
        if not choice.message.content.strip():
            raise LLMError("llm_empty_response")
        usage = payload.usage
        return CompletionResult(
            model=model,
            content=choice.message.content,
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
        )
