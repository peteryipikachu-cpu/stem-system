import asyncio

from app.worker import write_worker_heartbeat


class FakeRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def set(self, *args: object, **kwargs: object) -> None:
        self.calls.append((args, kwargs))


def test_worker_heartbeat_uses_expiring_redis_key() -> None:
    redis = FakeRedis()

    asyncio.run(write_worker_heartbeat(redis, "worker-test", 15))

    args, kwargs = redis.calls[0]
    assert str(args[0]).startswith("stem:workers:heartbeat:worker-test:")
    assert kwargs == {"ex": 15}
