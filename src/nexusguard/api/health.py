import asyncio
from collections.abc import Awaitable
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from nexusguard.infrastructure.cache import ping_cache
from nexusguard.infrastructure.database import ping_database

router = APIRouter(prefix="/health", tags=["health"])


class LivenessResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    dependencies: dict[str, Literal["up", "down"]]


@router.get("/live", response_model=LivenessResponse)
async def liveness() -> LivenessResponse:
    return LivenessResponse()


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse}},
)
async def readiness(request: Request) -> JSONResponse:
    async def check(probe: Awaitable[None]) -> Literal["up", "down"]:
        try:
            await asyncio.wait_for(
                probe, timeout=request.app.state.settings.dependency_timeout_seconds
            )
        except Exception:
            # Do not expose connection strings or infrastructure exception details.
            return "down"
        return "up"

    database, cache = await asyncio.gather(
        check(ping_database(request.app.state.engine)),
        check(ping_cache(request.app.state.cache)),
    )
    ready = database == cache == "up"
    body = ReadinessResponse(
        status="ready" if ready else "not_ready",
        dependencies={"database": database, "redis": cache},
    )
    return JSONResponse(
        status_code=200 if ready else 503,
        content=body.model_dump(),
        headers={"Cache-Control": "no-store"},
    )
