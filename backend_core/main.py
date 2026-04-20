"""
AURA Backend Core – FastAPI Application Entry Point
=====================================================
Bootstraps the API server, lifecycle events, and middleware.

CHANGES (refactor):
  - Migrated from deprecated @app.on_event to lifespan context manager
    (required as of FastAPI ≥0.93; on_event raises DeprecationWarning in 0.111).
  - Consolidated DatabaseManager to a SINGLE instance stored on app.state,
    eliminating the duplicate pool that was previously opened in routes.py.
    routes.py now reads the shared instance from request.app.state.db.
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend_core.api.routes import router
from backend_core.database.db import DatabaseManager

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
)
logger = logging.getLogger("backend_core")

# ---------------------------------------------------------------------------
# Shared singleton – created once, shared across all request handlers via
# app.state.db so that a single connection pool services the whole process.
# ---------------------------------------------------------------------------
_db = DatabaseManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage startup and shutdown of shared resources."""
    # ── Startup ──────────────────────────────────────────────────────────────
    await _db.connect()
    app.state.db = _db          # expose to routes via request.app.state.db
    logger.info("🚀 AURA Backend Core started.")

    yield   # ← server runs here

    # ── Shutdown ─────────────────────────────────────────────────────────────
    await _db.close()
    logger.info("🛑 AURA Backend Core stopped.")


app = FastAPI(
    title="AURA – Stadium Flow Intelligence API",
    version="1.0.0",
    description="Real-time crowd density management and rerouting system.",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS: restrict to specific origins in production via ALLOWED_ORIGINS env var
_allowed_origins = os.environ.get("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")

@app.get("/health")
async def health():
    """Health check endpoint for container orchestration."""
    return {"status": "healthy", "service": "aura-backend-core"}

# Mount the static dashboard frontend last so it doesn't intercept API routes
app.mount("/", StaticFiles(directory="dashboard", html=True), name="dashboard")
