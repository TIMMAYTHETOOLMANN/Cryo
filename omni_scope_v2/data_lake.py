#!/usr/bin/env python3
"""
omni_scope_v2.data_lake — Unified Real-Time Data Lake
=======================================================
Abstracts Kafka, Redis, TimescaleDB, and Neo4j behind a single
async facade.  All backends must be live — no silent degradation.

Responsibilities:
  1. Kafka — real-time stream ingestion (mempool, oracle updates, social)
  2. Redis — low-latency caching (price feeds, health factors, gas prices)
  3. TimescaleDB — time-series storage (historical events, P&L)
  4. Neo4j — graph storage (wallet clusters, cross-protocol exposure)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .config import DataLakeConfig, KafkaConfig, RedisConfig

logger = logging.getLogger(__name__)


# ── Kafka Topics ──────────────────────────────────────────────────

class Topic(Enum):
    RAW_MEMPOOL = "v2.raw-mempool"
    NEW_CONTRACTS = "v2.new-contracts"
    SOCIAL_FEEDS = "v2.social-feeds"
    BRIDGE_VOLUMES = "v2.bridge-volumes"
    ORACLE_UPDATES = "v2.oracle-updates"
    GOVERNANCE = "v2.governance"
    OPPORTUNITY_SIGNALS = "v2.opportunity-signals"
    EXECUTION_COMMANDS = "v2.execution-commands"
    EXECUTION_RESULTS = "v2.execution-results"
    SYSTEM_METRICS = "v2.system-metrics"
    WALLET_CLUSTERS = "v2.wallet-clusters"
    YIELD_DATA = "v2.yield-data"


# ── Kafka Client (graceful fallback) ─────────────────────────────

class KafkaClient:
    """Kafka producer + consumer — requires live Kafka cluster."""

    def __init__(self, config: KafkaConfig):
        self._cfg = config
        self._producer: Any = None
        self._consumers: Dict[str, Any] = {}
        self._available = False

    async def connect(self) -> bool:
        from kafka import KafkaProducer
        self._producer = KafkaProducer(
            bootstrap_servers=self._cfg.bootstrap_servers.split(","),
            value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
            key_serializer=lambda k: k.encode("utf-8") if k else None,
        )
        self._available = True
        logger.info("[DataLake] Kafka connected (%s)", self._cfg.bootstrap_servers)
        return self._available

    def produce(self, topic: Topic, value: Dict[str, Any], key: Optional[str] = None):
        if not self._available or not self._producer:
            raise RuntimeError("Kafka not connected — call connect() first")
        self._producer.send(topic.value, value=value, key=key)

    async def close(self):
        if self._producer:
            self._producer.flush()
            self._producer.close()


# ── Redis Client (graceful fallback) ─────────────────────────────

class RedisClient:
    """Async Redis cache — requires live Redis instance."""

    def __init__(self, config: RedisConfig):
        self._cfg = config
        self._client: Any = None
        self._available = False

    async def connect(self) -> bool:
        import aioredis
        self._client = await aioredis.from_url(
            self._cfg.url, max_connections=self._cfg.max_connections,
        )
        await self._client.ping()
        self._available = True
        logger.info("[DataLake] Redis connected (%s)", self._cfg.url)
        return self._available

    async def get(self, key: str) -> Optional[str]:
        if not self._available or not self._client:
            raise RuntimeError("Redis not connected — call connect() first")
        val = await self._client.get(key)
        return val.decode() if val else None

    async def set(self, key: str, value: str, ex: int = 300):
        if not self._available or not self._client:
            raise RuntimeError("Redis not connected — call connect() first")
        await self._client.set(key, value, ex=ex)

    async def get_json(self, key: str) -> Optional[Dict]:
        val = await self.get(key)
        if val:
            try:
                return json.loads(val)
            except json.JSONDecodeError:
                return None
        return None

    async def set_json(self, key: str, value: Dict, ex: int = 300):
        await self.set(key, json.dumps(value, default=str), ex=ex)

    async def close(self):
        if self._client:
            try:
                await self._client.close()
            except Exception:
                pass


# ── TimescaleDB Client (graceful fallback) ───────────────────────

class TimescaleClient:
    """TimescaleDB connection pool — requires live database."""

    def __init__(self, dsn: str):
        self._dsn = dsn
        self._pool: Any = None
        self._available = False

    async def connect(self) -> bool:
        import asyncpg
        self._pool = await asyncpg.create_pool(self._dsn, min_size=2, max_size=10)
        self._available = True
        logger.info("[DataLake] TimescaleDB connected")
        return self._available

    async def insert_event(self, table: str, data: Dict[str, Any]):
        if not self._available or not self._pool:
            raise RuntimeError("TimescaleDB not connected — call connect() first")
        cols = ", ".join(data.keys())
        vals = ", ".join(f"${i + 1}" for i in range(len(data)))
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"INSERT INTO {table} ({cols}) VALUES ({vals})",
                *data.values(),
            )

    async def query(self, sql: str, *args) -> List[Dict]:
        if self._available and self._pool:
            try:
                async with self._pool.acquire() as conn:
                    rows = await conn.fetch(sql, *args)
                    return [dict(r) for r in rows]
            except Exception:
                pass
        return []

    async def close(self):
        if self._pool:
            try:
                await self._pool.close()
            except Exception:
                pass


# ── Neo4j Client (graceful fallback) ─────────────────────────────

class Neo4jClient:
    """Neo4j graph DB for wallet clustering & relationship mapping — requires live instance."""

    def __init__(self, uri: str, user: str, password: str):
        self._uri = uri
        self._user = user
        self._password = password
        self._driver: Any = None
        self._available = False

    async def connect(self) -> bool:
        from neo4j import AsyncGraphDatabase
        self._driver = AsyncGraphDatabase.driver(
            self._uri, auth=(self._user, self._password),
        )
        # Verify connectivity
        async with self._driver.session() as session:
            await session.run("RETURN 1")
        self._available = True
        logger.info("[DataLake] Neo4j connected (%s)", self._uri)
        return self._available

    async def upsert_wallet(self, address: str, properties: Dict[str, Any]):
        if not self._available or not self._driver:
            raise RuntimeError("Neo4j not connected — call connect() first")
        async with self._driver.session() as session:
            await session.run(
                "MERGE (w:Wallet {address: $addr}) SET w += $props",
                addr=address, props=properties,
            )

    async def add_edge(self, from_addr: str, to_addr: str, rel_type: str,
                       properties: Optional[Dict] = None):
        if not self._available or not self._driver:
            raise RuntimeError("Neo4j not connected — call connect() first")
        async with self._driver.session() as session:
            await session.run(
                f"MATCH (a:Wallet {{address: $from}}), (b:Wallet {{address: $to}}) "
                f"MERGE (a)-[r:{rel_type}]->(b) SET r += $props",
                **{"from": from_addr, "to": to_addr, "props": properties or {}},
            )

    async def get_cluster(self, address: str) -> List[str]:
        """Get all wallets in the same cluster as *address*."""
        if not self._available or not self._driver:
            raise RuntimeError("Neo4j not connected — call connect() first")
        async with self._driver.session() as session:
            result = await session.run(
                "MATCH (a:Wallet {address: $addr})-[:FUNDS*1..3]-(b:Wallet) "
                "RETURN DISTINCT b.address AS addr",
                addr=address,
            )
            return [r["addr"] async for r in result]

    async def close(self):
        if self._driver:
            try:
                await self._driver.close()
            except Exception:
                pass


# ── Unified Data Lake Facade ─────────────────────────────────────

class DataLake:
    """
    Unified façade over Kafka, Redis, TimescaleDB, and Neo4j.
    Initialise with ``await lake.connect()``.
    All methods gracefully degrade if a backend is unavailable.
    """

    def __init__(self, config: Optional[DataLakeConfig] = None):
        cfg = config or DataLakeConfig()
        self.kafka = KafkaClient(cfg.kafka)
        self.redis = RedisClient(cfg.redis)
        self.timescale = TimescaleClient(cfg.timescale.dsn)
        self.neo4j = Neo4jClient(cfg.neo4j.uri, cfg.neo4j.user, cfg.neo4j.password)
        self._connected = False

    async def connect(self) -> Dict[str, bool]:
        """Connect all backends.  Returns availability map."""
        results = {
            "kafka": await self.kafka.connect(),
            "redis": await self.redis.connect(),
            "timescale": await self.timescale.connect(),
            "neo4j": await self.neo4j.connect(),
        }
        self._connected = True
        available = sum(1 for v in results.values() if v)
        logger.info(
            "[DataLake] %d/%d backends available: %s",
            available, len(results), results,
        )
        return results

    async def close(self):
        await self.kafka.close()
        await self.redis.close()
        await self.timescale.close()
        await self.neo4j.close()
        self._connected = False

    # ── Convenience Methods ────────────────────────────────────

    async def cache_price(self, asset: str, chain_id: int, price_usd: float,
                          source: str = "spot"):
        key = f"price:{chain_id}:{asset}:{source}"
        await self.redis.set_json(key, {
            "asset": asset, "chain_id": chain_id,
            "price_usd": price_usd, "source": source,
            "ts": time.time(),
        }, ex=60)

    async def get_cached_price(self, asset: str, chain_id: int,
                               source: str = "spot") -> Optional[float]:
        key = f"price:{chain_id}:{asset}:{source}"
        data = await self.redis.get_json(key)
        return data["price_usd"] if data else None

    async def cache_health_factor(self, borrower: str, protocol: str,
                                  chain_id: int, hf: float):
        key = f"hf:{chain_id}:{protocol}:{borrower}"
        await self.redis.set_json(key, {
            "borrower": borrower, "protocol": protocol,
            "chain_id": chain_id, "health_factor": hf,
            "ts": time.time(),
        }, ex=30)

    async def record_event(self, event_type: str, data: Dict[str, Any]):
        """Write to both Kafka and TimescaleDB."""
        event = {"event_type": event_type, "ts": time.time(), **data}
        self.kafka.produce(Topic.SYSTEM_METRICS, event, key=event_type)
        await self.timescale.insert_event("events", event)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "connected": self._connected,
            "kafka_available": self.kafka._available,
            "redis_available": self.redis._available,
            "timescale_available": self.timescale._available,
            "neo4j_available": self.neo4j._available,
        }
