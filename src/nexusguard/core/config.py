from functools import lru_cache
from typing import Literal

from pydantic import Field, HttpUrl, PostgresDsn, RedisDsn, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEXUSGUARD_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        hide_input_in_errors=True,
    )

    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    database_url: PostgresDsn = Field(repr=False)
    redis_url: RedisDsn = Field(repr=False)
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_max_overflow: int = Field(default=10, ge=0, le=100)
    dependency_timeout_seconds: float = Field(default=3.0, gt=0, le=30)

    reception_recent_order_days: int = Field(default=30, ge=1, le=365)

    agent_max_steps: int = Field(default=5, ge=2, le=8)

    llm_base_url: HttpUrl | None = None
    llm_api_key: SecretStr | None = Field(default=None, repr=False)
    llm_fast_model: str = Field(default="qwen3.8-flash", min_length=1)
    llm_reasoning_model: str = Field(default="qwen4-max", min_length=1)
    llm_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    llm_max_concurrency: int = Field(default=4, ge=1, le=100)
    llm_max_output_tokens: int = Field(default=512, ge=1, le=8192)

    @field_validator("llm_base_url")
    @classmethod
    def require_safe_llm_url(cls, value: HttpUrl | None) -> HttpUrl | None:
        if value is not None and (
            value.scheme != "https"
            or value.username
            or value.password
            or value.query
            or value.fragment
        ):
            raise ValueError("llm_base_url must use HTTPS without credentials, query or fragment")
        return value

    @field_validator("database_url")
    @classmethod
    def require_asyncpg(cls, value: PostgresDsn) -> PostgresDsn:
        if value.scheme != "postgresql+asyncpg":
            raise ValueError("database_url must use postgresql+asyncpg")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
