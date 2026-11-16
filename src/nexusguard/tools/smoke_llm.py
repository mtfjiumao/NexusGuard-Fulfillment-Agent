"""Two explicit, minimal live requests; not run by pytest or application startup."""

import asyncio
import json
from time import perf_counter

from pydantic import ValidationError

from nexusguard.core.config import get_settings
from nexusguard.infrastructure.llm import ChatMessage, LLMError, ModelGateway, ModelTier


async def run() -> int:
    try:
        gateway = ModelGateway(get_settings())
    except (LLMError, ValidationError):
        print(json.dumps({"status": "error", "code": "llm_configuration_error"}))
        return 1
    failed = False
    try:
        for tier in ModelTier:
            start = perf_counter()
            try:
                result = await gateway.complete(
                    tier,
                    [ChatMessage(role="user", content="Reply with exactly OK.")],
                    max_output_tokens=64,
                )
                print(
                    json.dumps(
                        {
                            "tier": tier.value,
                            "model": result.model,
                            "status": "ok",
                            "elapsed_ms": round((perf_counter() - start) * 1000),
                            "input_tokens": result.input_tokens,
                            "output_tokens": result.output_tokens,
                        }
                    )
                )
            except LLMError as error:
                failed = True
                print(
                    json.dumps(
                        {
                            "tier": tier.value,
                            "status": "error",
                            "code": error.code,
                            "http_status": error.status_code,
                        }
                    )
                )
    finally:
        await gateway.aclose()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
