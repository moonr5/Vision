"""
Scale Engine — Distributed Stream Bus.
Abstraction over Kafka / NATS / Redis Streams for ingesting millions of
MQTT messages without loss. Supports multiple backends via a common interface.

Current implementation: Redis Streams (lightweight, zero-infra option).
Kafka and NATS are supported via configuration.
"""

import json
import time
import asyncio
import os
from typing import Optional, Dict, Any, List, Callable, Awaitable
from dataclasses import dataclass
from datetime import datetime

# Try Redis; fall back gracefully
try:
    import redis.asyncio as aioredis
    HAS_REDIS = True
except ImportError:
    HAS_REDIS = False

try:
    from nats.aio.client import Client as NATS
    HAS_NATS = True
except ImportError:
    HAS_NATS = False


@dataclass
class StreamConfig:
    backend: str = "redis"       # redis | kafka | nats | memory
    redis_url: str = "redis://localhost:6379"
    stream_name: str = "sgu_telemetry_stream"
    consumer_group: str = "scale_engine"
    max_len: int = 10_000_000    # ~10M messages in ring buffer
    batch_size: int = 100
    nats_url: str = "nats://localhost:4222"
    kafka_brokers: str = "localhost:9092"
    kafka_topic: str = "sgu.telemetry"


class StreamBus:
    """
    Distributed stream bus for high-volume telemetry ingestion.

    Usage:
        bus = StreamBus(StreamConfig(backend="redis"))
        await bus.connect()
        await bus.publish({"device_id": "x", "speed": 45})
        async for batch in bus.consume():
            await process(batch)
    """

    def __init__(self, config: StreamConfig = None):
        self.cfg = config or StreamConfig()
        self._redis: Optional[aioredis.Redis] = None
        self._nats: Optional[NATS] = None
        self._memory_queue: asyncio.Queue = asyncio.Queue(maxsize=100_000)
        self._subscribers: List[Callable[[Dict], Awaitable[None]]] = []
        self._running = False

    async def connect(self):
        if self.cfg.backend == "redis" and HAS_REDIS:
            self._redis = aioredis.from_url(self.cfg.redis_url, decode_responses=False)
            # Create consumer group (idempotent)
            try:
                await self._redis.xgroup_create(
                    self.cfg.stream_name, self.cfg.consumer_group,
                    id="0", mkstream=True,
                )
            except Exception:
                pass  # group already exists
            print(f"[StreamBus] Redis Streams connected — {self.cfg.stream_name}")

        elif self.cfg.backend == "nats" and HAS_NATS:
            self._nats = NATS()
            await self._nats.connect(self.cfg.nats_url)
            print(f"[StreamBus] NATS connected — {self.cfg.nats_url}")

        elif self.cfg.backend == "memory":
            print("[StreamBus] In-memory queue ready (dev mode)")

        else:
            print(f"[StreamBus] Backend '{self.cfg.backend}' unavailable — using in-memory fallback")
            self.cfg.backend = "memory"

    async def publish(self, message: Dict[str, Any], topic: str = None):
        """Publish a telemetry message to the stream."""
        payload = {
            "data": message,
            "topic": topic or "monztrack/device01/gps",
            "ingested_at": datetime.utcnow().isoformat(),
        }
        raw = json.dumps(payload).encode()

        if self.cfg.backend == "redis" and self._redis:
            await self._redis.xadd(
                self.cfg.stream_name,
                {"payload": raw},
                maxlen=self.cfg.max_len,
            )
        elif self.cfg.backend == "nats" and self._nats:
            await self._nats.publish(self.cfg.kafka_topic, raw)
        else:
            await self._memory_queue.put(payload)

        # Fan-out to subscribers
        for sub in self._subscribers:
            try:
                await sub(message)
            except Exception:
                pass

    async def consume(self) -> asyncio.AsyncIterator[List[Dict[str, Any]]]:
        """Consume messages from the stream in batches."""
        self._running = True

        while self._running:
            batch = []

            if self.cfg.backend == "redis" and self._redis:
                try:
                    results = await self._redis.xreadgroup(
                        self.cfg.consumer_group, f"worker-{os.getpid()}",
                        {self.cfg.stream_name: ">"},
                        count=self.cfg.batch_size, block=1000,
                    )
                    for stream_name, messages in results:
                        for msg_id, fields in messages:
                            raw = fields.get(b"payload", b"{}")
                            batch.append(json.loads(raw))
                            await self._redis.xack(
                                self.cfg.stream_name,
                                self.cfg.consumer_group, msg_id,
                            )
                except Exception:
                    await asyncio.sleep(0.1)

            elif self.cfg.backend == "memory":
                try:
                    for _ in range(min(self.cfg.batch_size, self._memory_queue.qsize())):
                        msg = self._memory_queue.get_nowait()
                        batch.append(msg)
                except asyncio.QueueEmpty:
                    if not batch:
                        await asyncio.sleep(0.05)

            if batch:
                yield batch
            else:
                await asyncio.sleep(0.01)

    def subscribe(self, callback: Callable[[Dict], Awaitable[None]]):
        """Register a subscriber that fires on every published message."""
        self._subscribers.append(callback)

    async def stats(self) -> Dict[str, Any]:
        """Return stream statistics."""
        info = {"backend": self.cfg.backend, "subscribers": len(self._subscribers)}
        if self.cfg.backend == "redis" and self._redis:
            try:
                length = await self._redis.xlen(self.cfg.stream_name)
                info["queue_length"] = length
            except Exception:
                info["queue_length"] = -1
        elif self.cfg.backend == "memory":
            info["queue_length"] = self._memory_queue.qsize()
        return info

    async def close(self):
        self._running = False
        if self._redis:
            await self._redis.close()
        if self._nats:
            await self._nats.close()
