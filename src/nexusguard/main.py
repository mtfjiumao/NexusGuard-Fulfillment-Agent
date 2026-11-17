import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from time import perf_counter
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import async_sessionmaker

from nexusguard import __version__
from nexusguard.api.health import router as health_router
from nexusguard.api.reception_page import WEB_DIRECTORY
from nexusguard.api.reception_page import router as reception_page_router
from nexusguard.api.router import router as business_router
from nexusguard.core.config import Settings, get_settings
from nexusguard.core.logging import configure_logging
from nexusguard.infrastructure.cache import build_cache
from nexusguard.infrastructure.database import build_engine
from nexusguard.infrastructure.llm import ModelGateway

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            engine = build_engine(settings)
            stack.push_async_callback(engine.dispose)
            cache = build_cache(settings)
            stack.push_async_callback(cache.aclose)
            app.state.engine = engine
            app.state.cache = cache
            app.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)
            app.state.model_gateway = None
            if settings.llm_api_key and settings.llm_api_key.get_secret_value().strip():
                gateway = ModelGateway(settings)
                stack.push_async_callback(gateway.aclose)
                app.state.model_gateway = gateway
            yield

    app = FastAPI(
        title="NexusGuard Fulfillment Agent",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.environment != "production" else None,
    )
    app.state.settings = settings

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        candidate = request.headers.get("X-Request-ID", "")
        try:
            request_id = str(UUID(candidate))
        except ValueError:
            request_id = str(uuid4())
        request.state.request_id = request_id
        start = perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request_completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "status_code": response.status_code,
                "duration_ms": round((perf_counter() - start) * 1000, 2),
            },
        )
        return response

    app.include_router(health_router)
    app.include_router(business_router)
    app.include_router(reception_page_router)
    # Backend-only development remains available before the first frontend build.
    if WEB_DIRECTORY.is_dir():
        app.mount(
            "/assets/reception", StaticFiles(directory=WEB_DIRECTORY), name="reception_assets"
        )
    return app
