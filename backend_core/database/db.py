"""
AURA Backend Core – Database Manager
======================================
Async PostgreSQL interface using asyncpg.
Falls back to in-memory store when DB is unavailable (graceful degradation).

WHY asyncpg over SQLAlchemy:
  asyncpg is 3-5x faster for high-throughput async inserts.
  We don't need ORM complexity for this append-heavy workload.
"""

import json
import logging
import os
from collections import deque
from typing import Any, Dict

logger = logging.getLogger(__name__)

try:
    import asyncpg
    ASYNCPG_AVAILABLE = True
except ImportError:
    ASYNCPG_AVAILABLE = False
    logger.warning("asyncpg not installed; using in-memory fallback.")


class DatabaseManager:
    """
    Manages async database operations.
    All methods are safe to call even when DB is offline (no-op fallback).
    """

    def __init__(self):
        self._pool = None
        # Bounded fallback queues: prevent unbounded growth if DB stays offline.
        # maxlen=500 keeps ~8 min of 1 Hz data per log type before eviction.
        self._fallback_store = {
            "density_logs": deque(maxlen=500),
            "nudge_logs":   deque(maxlen=500),
            "path_logs":    deque(maxlen=500),
        }
        self._db_url = os.environ.get(
            "DATABASE_URL",
            "postgresql://aura:aura_pass@db:5432/aura_db",
        )

    async def connect(self) -> None:
        """Create connection pool. Called once at app startup."""
        if not ASYNCPG_AVAILABLE:
            return
        try:
            self._pool = await asyncpg.create_pool(self._db_url, min_size=2, max_size=10)
            logger.info("✅ Database pool established.")
        except Exception as exc:
            logger.error("DB connect failed (using fallback): %s", exc)
            self._pool = None

    async def log_density(self, reading: Dict[str, Any]) -> None:
        """Insert a density reading into density_logs."""
        if self._pool:
            try:
                async with self._pool.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO density_logs
                          (section_id, density_score, raw_density, person_count, timestamp, threshold_breached)
                        VALUES ($1, $2, $3, $4, to_timestamp($5), $6)
                        """,
                        reading.get("section_id", "UNKNOWN"),
                        reading.get("density_score", 0.0),
                        reading.get("raw_density", 0.0),
                        reading.get("person_count", 0),
                        reading.get("timestamp", 0.0),
                        reading.get("threshold_breached", False),
                    )
            except Exception as exc:
                logger.error("DB write error (density_logs): %s", exc)
        else:
            self._fallback_store["density_logs"].append(reading)

    async def log_nudge(self, nudge: Dict[str, Any]) -> None:
        """Insert a nudge action into nudge_logs."""
        if self._pool:
            try:
                async with self._pool.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO nudge_logs
                          (action_id, section_from, section_to, nudge_type, value, reason, rl_confidence, timestamp)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, to_timestamp($8))
                        """,
                        nudge["action_id"],
                        nudge["section_from"],
                        nudge["section_to"],
                        nudge["nudge_type"],
                        nudge["value"],
                        nudge["reason"],
                        nudge["rl_confidence"],
                        nudge["timestamp"],
                    )
            except Exception as exc:
                logger.error("DB write error (nudge_logs): %s", exc)
        else:
            self._fallback_store["nudge_logs"].append(nudge)

    async def log_path_decision(self, path: Dict[str, Any]) -> None:
        """Insert a path decision into path_decisions."""
        if self._pool:
            try:
                async with self._pool.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO path_decisions
                          (path_json, total_cost, reasoning, created_at)
                        VALUES ($1, $2, $3, NOW())
                        """,
                        json.dumps(path["path"]),
                        path["total_cost"],
                        path["reasoning"],
                    )
            except Exception as exc:
                logger.error("DB write error (path_decisions): %s", exc)
        else:
            self._fallback_store["path_logs"].append(path)

    async def close(self) -> None:
        """Gracefully close the connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None   # prevent use-after-close on subsequent log calls
