"""
AURA – Test Fixtures (conftest.py)
=====================================
Shared pytest fixtures used across all test modules.

Design decisions:
  - All external dependencies (DB, GCP) are mocked to ensure tests run
    without any live services or credentials.
  - The FastAPI TestClient overrides the `get_db` dependency with an
    in-memory mock so no asyncpg connection is ever attempted.
  - NudgeEngine and AStarPathfinder are instantiated directly; they have
    no external dependencies and are safe to use in tests as-is.
  - GCPPublisher and GCPMetricsReporter are patched before routes import
    to prevent GCP SDK init warnings in test output.
"""

import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ── Path setup ────────────────────────────────────────────────────────────
# Ensure AURA-STADIUM-FLOW root is importable when running pytest from
# within the project directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ── Service fixtures ──────────────────────────────────────────────────────

@pytest.fixture()
def nudge_engine():
    """Fresh NudgeEngine with no prior state."""
    from backend_core.services.nudge_engine import NudgeEngine
    return NudgeEngine()


@pytest.fixture()
def rule_engine():
    """Isolated RuleEngine instance."""
    from backend_core.services.nudge_engine import RuleEngine
    return RuleEngine()


@pytest.fixture()
def rl_optimizer():
    """Isolated RLNudgeOptimizer instance."""
    from backend_core.services.nudge_engine import RLNudgeOptimizer
    return RLNudgeOptimizer()


@pytest.fixture()
def pathfinder():
    """AStarPathfinder with default stadium graph."""
    from backend_core.services.pathfinder import AStarPathfinder
    return AStarPathfinder()


# ── Database mock ─────────────────────────────────────────────────────────

@pytest.fixture()
def mock_db():
    """
    AsyncMock of DatabaseManager.
    All log_* methods are no-ops that return immediately.
    """
    db = MagicMock()
    db.log_density = AsyncMock(return_value=None)
    db.log_nudge = AsyncMock(return_value=None)
    db.log_path_decision = AsyncMock(return_value=None)
    db.close = AsyncMock(return_value=None)
    return db


# ── FastAPI TestClient ─────────────────────────────────────────────────────

@pytest.fixture()
def client(mock_db):
    """
    FastAPI TestClient with:
      - DB dependency overridden to mock_db (no DB connection required)
      - GCPPublisher and GCPMetricsReporter patched to no-op
    """
    with (
        patch("backend_core.services.gcp_publisher._PUBSUB_AVAILABLE", False),
        patch("backend_core.services.gcp_metrics._MONITORING_AVAILABLE", False),
    ):
        # Import app AFTER patches are applied
        from backend_core.main import app
        from backend_core.api.routes import get_db

        app.dependency_overrides[get_db] = lambda: mock_db

        # TestClient handles lifespan (startup/shutdown) automatically
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c

        app.dependency_overrides.clear()
