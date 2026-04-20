"""
Tests – DatabaseManager (Fallback Behaviour)
=============================================
These tests verify the in-memory fallback store used when asyncpg is
unavailable or the DB connection fails. No live PostgreSQL is required.

Coverage:
  - log_density()        → fallback store append
  - log_nudge()          → fallback store append
  - log_path_decision()  → fallback store append
  - Fallback deque maxlen (bounded growth)
  - close() → pool reset to None (safe double-close)
  - connect() → no-op when asyncpg unavailable
"""

import pytest
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock


# ══════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════


@pytest.fixture()
def db_no_pool():
    """DatabaseManager with _pool forced to None (simulates no DB connection)."""
    from backend_core.database.db import DatabaseManager
    dm = DatabaseManager()
    dm._pool = None   # ensure no real pool is used
    return dm


# ══════════════════════════════════════════════════════════════════
# log_density
# ══════════════════════════════════════════════════════════════════


class TestLogDensity:
    def test_appends_to_fallback_when_no_pool(self, db_no_pool):
        reading = {
            "section_id": "C", "density_score": 0.87,
            "raw_density": 0.85, "person_count": 174,
            "timestamp": 1712860000.0, "threshold_breached": True,
        }
        asyncio.get_event_loop().run_until_complete(db_no_pool.log_density(reading))
        store = list(db_no_pool._fallback_store["density_logs"])
        assert len(store) == 1
        assert store[0]["section_id"] == "C"

    def test_multiple_appends(self, db_no_pool):
        async def run():
            for i in range(3):
                await db_no_pool.log_density({"section_id": "A", "density_score": 0.5 + i * 0.1,
                                              "raw_density": 0.5, "person_count": 100,
                                              "timestamp": 1000.0 + i, "threshold_breached": False})
        asyncio.get_event_loop().run_until_complete(run())
        assert len(db_no_pool._fallback_store["density_logs"]) == 3

    def test_fallback_deque_bounded(self, db_no_pool):
        """Writing 600 entries should not exceed maxlen=500."""
        async def run():
            for i in range(600):
                await db_no_pool.log_density({
                    "section_id": "B", "density_score": 0.5,
                    "raw_density": 0.5, "person_count": 50,
                    "timestamp": float(i), "threshold_breached": False,
                })
        asyncio.get_event_loop().run_until_complete(run())
        assert len(db_no_pool._fallback_store["density_logs"]) == 500


# ══════════════════════════════════════════════════════════════════
# log_nudge
# ══════════════════════════════════════════════════════════════════


class TestLogNudge:
    NUDGE = {
        "action_id": "nudge-C-abc123",
        "section_from": "C",
        "section_to": "D",
        "nudge_type": "discount",
        "value": "20% off",
        "reason": "density 87%",
        "rl_confidence": 0.82,
        "timestamp": 1712860000.0,
    }

    def test_appends_to_nudge_fallback(self, db_no_pool):
        asyncio.get_event_loop().run_until_complete(db_no_pool.log_nudge(self.NUDGE))
        store = list(db_no_pool._fallback_store["nudge_logs"])
        assert len(store) == 1
        assert store[0]["action_id"] == "nudge-C-abc123"

    def test_nudge_fallback_bounded(self, db_no_pool):
        async def run():
            for i in range(510):
                nudge = {**self.NUDGE, "action_id": f"nudge-C-{i}"}
                await db_no_pool.log_nudge(nudge)
        asyncio.get_event_loop().run_until_complete(run())
        assert len(db_no_pool._fallback_store["nudge_logs"]) == 500


# ══════════════════════════════════════════════════════════════════
# log_path_decision
# ══════════════════════════════════════════════════════════════════


class TestLogPathDecision:
    PATH = {
        "path": ["C", "F", "D"],
        "total_cost": 2.4,
        "segments": [{"section_from": "C", "section_to": "F", "cost": 1.2}],
        "reasoning": "Avoiding congested section A",
    }

    def test_appends_to_path_fallback(self, db_no_pool):
        asyncio.get_event_loop().run_until_complete(db_no_pool.log_path_decision(self.PATH))
        store = list(db_no_pool._fallback_store["path_logs"])
        assert len(store) == 1
        assert store[0]["total_cost"] == pytest.approx(2.4)


# ══════════════════════════════════════════════════════════════════
# close() and connect()
# ══════════════════════════════════════════════════════════════════


class TestLifecycle:
    def test_close_with_no_pool_is_safe(self, db_no_pool):
        """close() must not raise when pool is already None."""
        asyncio.get_event_loop().run_until_complete(db_no_pool.close())
        assert db_no_pool._pool is None

    def test_close_sets_pool_to_none(self):
        from backend_core.database.db import DatabaseManager
        dm = DatabaseManager()
        mock_pool = AsyncMock()
        dm._pool = mock_pool
        asyncio.get_event_loop().run_until_complete(dm.close())
        assert dm._pool is None
        mock_pool.close.assert_called_once()

    def test_connect_noop_without_asyncpg(self, db_no_pool):
        """connect() should be a no-op when asyncpg is unavailable."""
        import backend_core.database.db as db_module
        original = db_module.ASYNCPG_AVAILABLE
        db_module.ASYNCPG_AVAILABLE = False
        try:
            asyncio.get_event_loop().run_until_complete(db_no_pool.connect())
            assert db_no_pool._pool is None
        finally:
            db_module.ASYNCPG_AVAILABLE = original

    def test_fallback_store_has_correct_keys(self, db_no_pool):
        expected_keys = {"density_logs", "nudge_logs", "path_logs"}
        assert set(db_no_pool._fallback_store.keys()) == expected_keys
