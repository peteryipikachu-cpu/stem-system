from __future__ import annotations

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://stem:stem@postgres:5432/stem_audit"
    redis_url: str = "redis://redis:6379/0"
    worker_id: str = "worker-1"
    worker_concurrency: int = 12
    worker_heartbeat_ttl_seconds: int = 15
    # 豆包深度思考可能持续数十分钟；租约必须覆盖最长的供应商读取超时。
    lease_seconds: int = 3_900
    # 工作项执行期间持续刷新租约；流式响应到达时则额外写入片段观测。
    ai_work_lease_heartbeat_seconds: int = 30
    ai_stream_observation_interval_seconds: int = 2
    poll_interval_ms: int = 500
    ai_queue_max_wait_ms: int = 900_000
    # 通用供应商（例如 Gemini）的读取超时。
    ai_model_read_timeout_seconds: int = 600
    # GLM-5.2 的深度思考可能长时间不返回首个流式片段；单独保留一小时读取窗口。
    ai_glm_read_timeout_seconds: int = 3_600
    # 豆包深度思考专用读取超时。APIRoute 在模型完成前不一定返回首个响应字节。
    ai_doubao_read_timeout_seconds: int = 3_600
    # Number of retries after the initial provider request.
    ai_retry_max_attempts: int = 1
    ai_limit_rule_concurrency: int = 4
    ai_limit_doubao_concurrency: int = 3
    ai_limit_doubao_deep_concurrency: int = 2
    ai_limit_doubao_fast_concurrency: int = 1
    ai_limit_gemini_concurrency: int = 2
    ai_limit_gemini_answer_concurrency: int = 2
    ai_limit_gemini_synthesis_concurrency: int = 1
    ai_limit_apiroute_concurrency: int = 3
    ai_limit_apiroute_lane_concurrency: int = 2
    ai_limit_doubao_rpm: int = 0
    ai_limit_doubao_tpm: int = 0
    ai_limit_gemini_rpm: int = 0
    ai_limit_gemini_tpm: int = 0
    ai_limit_apiroute_rpm: int = 0
    ai_limit_apiroute_tpm: int = 0
    provider_circuit_failure_threshold: int = 3
    provider_circuit_window_seconds: int = 300
    provider_circuit_open_seconds: int = 300
    batch_deadline_timezone: str = "Asia/Shanghai"
    batch_deadline_hour: int = 8
    batch_manual_review_cutoff_minutes: int = 30
    batch_estimated_model_p95_seconds: int = 60
    # One shared APIRoute key pool for every supported audit model.
    apiroute_api_keys: str = ""
    apiroute_base_url: str = "https://apiroute.bodenai.net/v1"

    @property
    def apiroute_keys(self) -> list[str]:
        values = [key.strip() for key in self.apiroute_api_keys.split(",") if key.strip()]
        return list(dict.fromkeys(values))


@lru_cache
def get_settings() -> Settings:
    return Settings()
