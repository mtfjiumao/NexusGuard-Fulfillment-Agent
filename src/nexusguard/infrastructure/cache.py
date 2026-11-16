from redis.asyncio import Redis

from nexusguard.core.config import Settings


def build_cache(settings: Settings) -> Redis:
    return Redis.from_url(
        str(settings.redis_url),
        decode_responses=True,
        socket_connect_timeout=settings.dependency_timeout_seconds,
        socket_timeout=settings.dependency_timeout_seconds,
    )


async def ping_cache(cache: Redis) -> None:
    await cache.ping()
