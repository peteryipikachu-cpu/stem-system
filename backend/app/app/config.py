from __future__ import annotations

from functools import lru_cache
from typing import Optional
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", ".env.local"), extra="ignore")

    database_url: str = "postgresql+asyncpg://stem:stem@postgres:5432/stem_audit"
    redis_url: str = "redis://redis:6379/0"
    cors_origins: str = "http://localhost:3000"
    worker_id: str = "worker-1"
    worker_concurrency: int = 8
    # 单次深度推理可持续 30–50 分钟；租约必须覆盖模型读取超时，避免调用尚未结束就被重复领取。
    lease_seconds: int = 3_900
    poll_interval_ms: int = 500
    ai_queue_max_wait_ms: int = 900_000
    # 上游深度推理允许最长一小时无响应。可按供应商 SLA 通过环境变量覆盖。
    ai_model_read_timeout_seconds: int = 3_600
    # Number of retries after the initial provider request.
    ai_retry_max_attempts: int = 3
    ai_limit_rule_concurrency: int = 4
    ai_limit_doubao_concurrency: int = 3
    ai_limit_doubao_deep_concurrency: int = 2
    ai_limit_doubao_fast_concurrency: int = 1
    ai_limit_gemini_concurrency: int = 2
    ai_limit_gemini_answer_concurrency: int = 2
    ai_limit_gemini_synthesis_concurrency: int = 1
    ai_limit_doubao_rpm: int = 0
    ai_limit_doubao_tpm: int = 0
    ai_limit_gemini_rpm: int = 0
    ai_limit_gemini_tpm: int = 0
    provider_circuit_failure_threshold: int = 3
    provider_circuit_window_seconds: int = 300
    provider_circuit_open_seconds: int = 300
    batch_deadline_timezone: str = "Asia/Shanghai"
    batch_deadline_hour: int = 8
    batch_manual_review_cutoff_minutes: int = 30
    batch_estimated_model_p95_seconds: int = 60
    doubao_api_keys: str = ""
    doubao_api_key: Optional[str] = None
    # Generic APIRoute pool. The same gateway keys work for Doubao and Gemini.
    apiroute_api_keys: str = ""
    apiroute_api_key: Optional[str] = None
    # APIRoute exposes the Doubao model through an OpenAI-compatible API.
    doubao_model: str = "doubao-seed-2-0-pro-260215"
    doubao_base_url: str = "https://apiroute.bodenai.net/v1"
    gemini_api_keys: str = ""
    gemini_api_key: Optional[str] = None
    gemini_model: str = "gemini-3.1-pro-preview"
    gemini_base_url: str = "https://apiroute.bodenai.net/v1"
    # 首次启动时自动创建的管理员。生产环境必须通过环境变量覆盖默认密码。
    initial_admin_username: str = "admin"
    initial_admin_password: str = "Admin@123456"
    auth_secret: str = "change-this-secret-before-production"
    auth_session_hours: int = 24
    auth_cookie_secure: bool = False

    @staticmethod
    def _keys(pool: str, fallback: Optional[str]) -> list[str]:
        values = [value.strip() for value in pool.split(",") if value.strip()]
        if fallback and fallback.strip():
            values.append(fallback.strip())
        return list(dict.fromkeys(values))

    @property
    def doubao_keys(self) -> list[str]:
        return self._keys(self.doubao_api_keys, self.doubao_api_key)

    @property
    def gemini_keys(self) -> list[str]:
        return self._keys(self.gemini_api_keys, self.gemini_api_key)

    @property
    def apiroute_keys(self) -> list[str]:
        return self._keys(self.apiroute_api_keys, self.apiroute_api_key)

    @field_validator("database_url")
    @classmethod
    def use_asyncpg_database_driver(cls, value: str) -> str:
        if value.startswith("postgresql://"):
            return "postgresql+asyncpg://" + value.removeprefix("postgresql://")
        if value.startswith("postgres://"):
            return "postgresql+asyncpg://" + value.removeprefix("postgres://")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
