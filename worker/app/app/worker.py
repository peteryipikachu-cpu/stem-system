import asyncio
import os
import time
from redis.asyncio import Redis
from .config import get_settings
from .db import SessionLocal
from .services import (
    move_batch_cutoff_to_manual_review,
    recover_expired_leases,
    recover_queued_work,
    recover_ready_dependencies,
    reconcile_orphaned_runs,
    worker_once,
)


def worker_heartbeat_key(worker_id: str) -> str:
    return f"stem:workers:heartbeat:{worker_id}:{os.getpid()}"


async def write_worker_heartbeat(redis: Redis, worker_id: str, ttl_seconds: int) -> None:
    await redis.set(worker_heartbeat_key(worker_id), str(time.time()), ex=max(1, ttl_seconds))


async def run() -> None:
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url)
    active: set[asyncio.Task[int]] = set()

    async def process_one() -> int:
        async with SessionLocal() as session:
            return await worker_once(session, redis, settings)

    async def recover_leases() -> None:
        async with SessionLocal() as session:
            recovered = await recover_expired_leases(session, redis)
            dependencies = await recover_ready_dependencies(session, redis)
            await recover_queued_work(session, redis)
            cutoff = await move_batch_cutoff_to_manual_review(session, redis, settings)
            reconciled = await reconcile_orphaned_runs(session, redis)
            if recovered or dependencies or cutoff or reconciled:
                await session.commit()

    try:
        last_recovery = 0.0
        while True:
            if time.monotonic() - last_recovery >= 5:
                await write_worker_heartbeat(redis, settings.worker_id, settings.worker_heartbeat_ttl_seconds)
                await recover_leases()
                last_recovery = time.monotonic()
            while len(active) < settings.worker_concurrency:
                active.add(asyncio.create_task(process_one()))
            done, active = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
            ran = 0
            for task in done:
                try:
                    ran += task.result()
                except Exception:
                    # 单个工作项的异常已在 worker_once 内持久化；不能让它终止整个调度循环。
                    continue
            if ran == 0:
                await asyncio.sleep(settings.poll_interval_ms / 1000)
    finally:
        # 关闭时先让正在执行的模型调用回到 worker_once 的取消处理，
        # 将已领取的工作项归还队列，避免下次启动留下永久 running 状态。
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(run())
