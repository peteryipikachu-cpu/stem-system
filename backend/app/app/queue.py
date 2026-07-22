from __future__ import annotations

"""Redis-coordinated global quotas and durable-queue acceleration."""
from dataclasses import dataclass
from redis.asyncio import Redis
from .config import Settings

ACQUIRE_LUA = """
local total_inflight = tonumber(redis.call('GET', KEYS[1]) or '0')
local lane_inflight = tonumber(redis.call('GET', KEYS[2]) or '0')
local max_total = tonumber(ARGV[1])
local max_lane = tonumber(ARGV[2])
if total_inflight >= max_total or lane_inflight >= max_lane then return 0 end
local rpm = tonumber(ARGV[3])
local tpm = tonumber(ARGV[4])
local estimated_tokens = tonumber(ARGV[5])
local now = tonumber(ARGV[6])
local window = math.floor(now / 60000)
if rpm > 0 then
  local request_key = KEYS[3] .. ':' .. window
  local current = tonumber(redis.call('INCR', request_key))
  if current == 1 then redis.call('PEXPIRE', request_key, 61000) end
  if current > rpm then redis.call('DECR', request_key); return 0 end
end
if tpm > 0 then
  local token_key = KEYS[4] .. ':' .. window
  local current_tokens = tonumber(redis.call('INCRBY', token_key, estimated_tokens))
  if current_tokens == estimated_tokens then redis.call('PEXPIRE', token_key, 61000) end
  if current_tokens > tpm then
    redis.call('DECRBY', token_key, estimated_tokens)
    if rpm > 0 then redis.call('DECR', KEYS[3] .. ':' .. window) end
    return 0
  end
end
redis.call('INCR', KEYS[1]); redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[7]))
redis.call('INCR', KEYS[2]); redis.call('PEXPIRE', KEYS[2], tonumber(ARGV[7]))
return 1
"""
RELEASE_LUA = """
local total = tonumber(redis.call('GET', KEYS[1]) or '0')
local lane = tonumber(redis.call('GET', KEYS[2]) or '0')
if total > 0 then redis.call('DECR', KEYS[1]) end
if lane > 0 then redis.call('DECR', KEYS[2]) end
return 1
"""

# Claim only a work item whose ZSET score (available_at) has elapsed.  A plain
# ZPOPMIN would incorrectly consume delayed retries before their backoff ends.
POP_READY_LUA = """
local ready = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, 1)
if #ready == 0 then return false end
if redis.call('ZREM', KEYS[1], ready[1]) == 1 then return ready[1] end
return false
"""

# Batch owners are stored in a second ZSET ordered by their last dispatch time.
# This yields round-robin fairness without weakening the batch-over-interactive
# priority policy.
POP_BATCH_LUA = """
local owners = redis.call('ZRANGE', KEYS[1], 0, -1)
for _, owner in ipairs(owners) do
  local owner_key = 'stem:ready:batch:owner:' .. owner
  local ready = redis.call('ZRANGEBYSCORE', owner_key, '-inf', ARGV[1], 'LIMIT', 0, 1)
  if #ready > 0 then
    if redis.call('ZREM', owner_key, ready[1]) == 1 then
      if redis.call('ZCARD', owner_key) == 0 then
        redis.call('ZREM', KEYS[1], owner)
      else
        redis.call('ZADD', KEYS[1], ARGV[1], owner)
      end
      return ready[1]
    end
  elseif redis.call('ZCARD', owner_key) == 0 then
    redis.call('ZREM', KEYS[1], owner)
  end
end
return false
"""


@dataclass(frozen=True)
class ProviderLimit:
    total_concurrency: int
    lane_concurrency: int
    lane: str
    rpm: int
    tpm: int


def provider_limit(settings: Settings, provider: str, stage: str) -> ProviderLimit:
    if provider == "doubao":
        lane = "fast" if stage == "equivalence" else "deep"
        return ProviderLimit(settings.ai_limit_doubao_concurrency, settings.ai_limit_doubao_fast_concurrency if lane == "fast" else settings.ai_limit_doubao_deep_concurrency, lane, settings.ai_limit_doubao_rpm, settings.ai_limit_doubao_tpm)
    if provider == "gemini":
        lane = "synthesis" if stage == "synthesis" else "answer"
        return ProviderLimit(settings.ai_limit_gemini_concurrency, settings.ai_limit_gemini_synthesis_concurrency if lane == "synthesis" else settings.ai_limit_gemini_answer_concurrency, lane, settings.ai_limit_gemini_rpm, settings.ai_limit_gemini_tpm)
    return ProviderLimit(settings.ai_limit_rule_concurrency, settings.ai_limit_rule_concurrency, "default", 0, 0)


async def acquire(redis: Redis, settings: Settings, provider: str, stage: str, now_ms: int, estimated_tokens: int = 0) -> bool:
    limit = provider_limit(settings, provider, stage)
    total_key = f"stem:limit:{provider}:total:inflight"
    lane_key = f"stem:limit:{provider}:lane:{limit.lane}:inflight"
    rpm_key = f"stem:limit:{provider}:rpm"
    tpm_key = f"stem:limit:{provider}:tpm"
    return bool(await redis.eval(ACQUIRE_LUA, 4, total_key, lane_key, rpm_key, tpm_key, limit.total_concurrency, limit.lane_concurrency, limit.rpm, limit.tpm, max(1, estimated_tokens), now_ms, settings.lease_seconds * 1000))


async def release(redis: Redis, settings: Settings, provider: str, stage: str) -> None:
    limit = provider_limit(settings, provider, stage)
    await redis.eval(RELEASE_LUA, 2, f"stem:limit:{provider}:total:inflight", f"stem:limit:{provider}:lane:{limit.lane}:inflight")


async def pop_ready(redis: Redis, priority: str, now_timestamp: float) -> str | None:
    if priority == "batch":
        result = await redis.eval(POP_BATCH_LUA, 1, "stem:ready:batch:owners", now_timestamp)
    else:
        result = await redis.eval(POP_READY_LUA, 1, f"stem:ready:{priority}", now_timestamp)
    if not result:
        return None
    return result.decode() if isinstance(result, bytes) else str(result)
