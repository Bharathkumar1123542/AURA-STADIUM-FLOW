"""
AURA Backend Core – FastAPI Routes
=====================================
All HTTP endpoints for the AURA system.

Design decisions:
- Async handlers: FastAPI + asyncio handles concurrent camera feeds
- Single DB instance: accessed via request.app.state.db (set in lifespan)
- Pydantic v2: uses @field_validator (replacing deprecated @validator)
- Structured errors: always return {error: str} with appropriate HTTP codes

CHANGES (refactor):
  - Removed the module-level DatabaseManager() instantiation (was B7).
    DB is now read from request.app.state.db, which is the single connected
    pool created in main.py lifespan.
  - @validator replaced with @field_validator + mode="before" (Pydantic v2, B9).
  - RerouteRequest now carries its own field validators; /reroute-path drops
    the duplicated manual validation logic (B10).
  - NOTE: _density_state is kept as a module-level dict.  With uvicorn in
    single-worker mode (see Dockerfile) this is safe.  Multi-worker deployments
    MUST migrate this to Redis.  A TODO comment marks the upgrade path.
"""

import logging
import time
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, Field, field_validator

from backend_core.services.nudge_engine import NudgeEngine
from backend_core.services.pathfinder import AStarPathfinder
from backend_core.database.db import DatabaseManager

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic Request / Response Models (enforces API contract at runtime)
# ---------------------------------------------------------------------------

_VALID_SECTIONS = {"A", "B", "C", "D", "E", "F"}


class DensityUpdateRequest(BaseModel):
    section_id: str = Field(..., description="Stadium section identifier",
                            json_schema_extra={"example": "C"})
    density_score: float = Field(..., ge=0.0, le=1.0,
                                 json_schema_extra={"example": 0.87})
    raw_density: float = Field(0.0, ge=0.0, le=1.0)
    person_count: int = Field(0, ge=0)
    capacity: int = Field(200, gt=0)
    timestamp: float = Field(default_factory=time.time)
    threshold_breached: bool = False
    predicted_density_10min: float = Field(0.0, ge=0.0, le=1.0)

    @field_validator("section_id", mode="before")
    @classmethod
    def section_must_be_valid(cls, v: str) -> str:
        if str(v).upper() not in _VALID_SECTIONS:
            raise ValueError(f"section_id must be one of {_VALID_SECTIONS}")
        return str(v).upper()


class RerouteRequest(BaseModel):
    start_section: str = Field(..., json_schema_extra={"example": "C"})
    goal_section: str = Field(..., json_schema_extra={"example": "D"})

    @field_validator("start_section", "goal_section", mode="before")
    @classmethod
    def section_must_be_valid(cls, v: str) -> str:
        if str(v).upper() not in _VALID_SECTIONS:
            raise ValueError(f"section must be one of {_VALID_SECTIONS}")
        return str(v).upper()


class NudgeFeedbackRequest(BaseModel):
    """Allow external feedback for RL training."""
    section_id: str
    nudge_type: str
    reward: float = Field(..., ge=0.0, le=1.0,
                          description="Crowd movement success rate (0=no movement, 1=fully moved)")

    @field_validator("section_id", mode="before")
    @classmethod
    def section_must_be_valid(cls, v: str) -> str:
        if str(v).upper() not in _VALID_SECTIONS:
            raise ValueError(f"section_id must be one of {_VALID_SECTIONS}")
        return str(v).upper()


class DensityUpdateResponse(BaseModel):
    status: str
    nudge_triggered: bool
    nudge_action: Optional[dict] = None
    predicted_congestion_10min: float


# ---------------------------------------------------------------------------
# Dependency Providers (singletons shared across requests)
# ---------------------------------------------------------------------------

# NudgeEngine and AStarPathfinder are stateless-enough for in-process sharing
_nudge_engine = NudgeEngine()
_pathfinder = AStarPathfinder()

# TODO (multi-worker upgrade): replace with Redis hash so all uvicorn workers
# share a consistent density snapshot.  Current single-worker mode is safe.
_density_state: Dict[str, float] = {}


def get_nudge_engine() -> NudgeEngine:
    return _nudge_engine


def get_pathfinder() -> AStarPathfinder:
    return _pathfinder


def get_db(request: Request) -> DatabaseManager:
    """Return the single connected DatabaseManager from app.state (lifespan)."""
    return request.app.state.db


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/density-update", response_model=DensityUpdateResponse)
async def density_update(
    req: DensityUpdateRequest,
    nudge_engine: NudgeEngine = Depends(get_nudge_engine),
    db: DatabaseManager = Depends(get_db),
):
    """
    Receive a density reading from the camera engine.
    Evaluates whether a nudge should be triggered.
    Stores reading in database for historical analysis.
    """
    # Update shared density map
    _density_state[req.section_id] = req.density_score

    # Persist to DB
    await db.log_density(req.model_dump())

    # Nudge evaluation
    nudge_action = nudge_engine.evaluate(
        section_id=req.section_id,
        density_score=req.density_score,
        predicted_density=req.predicted_density_10min,
    )

    nudge_dict = None
    if nudge_action:
        nudge_dict = nudge_action.to_dict()
        await db.log_nudge(nudge_dict)
        logger.info("Nudge persisted: %s", nudge_action.action_id)

    return DensityUpdateResponse(
        status="ok",
        nudge_triggered=nudge_action is not None,
        nudge_action=nudge_dict,
        predicted_congestion_10min=req.predicted_density_10min,
    )


@router.get("/reroute-path")
async def reroute_path(
    start: str,
    goal: str,
    pathfinder: AStarPathfinder = Depends(get_pathfinder),
    db: DatabaseManager = Depends(get_db),
):
    """
    Return optimal path from start to goal, avoiding congested sections.
    Fan App uses this to display the green LED path.
    Validation delegated to the validator helper to keep DRY.
    """
    # Normalise and validate via the same logic as RerouteRequest
    start = start.upper()
    goal = goal.upper()
    if start not in _VALID_SECTIONS:
        raise HTTPException(status_code=400, detail=f"Invalid start section. Valid: {_VALID_SECTIONS}")
    if goal not in _VALID_SECTIONS:
        raise HTTPException(status_code=400, detail=f"Invalid goal section. Valid: {_VALID_SECTIONS}")

    result = pathfinder.find_path(
        start=start,
        goal=goal,
        densities=_density_state,
    )

    if not result:
        raise HTTPException(status_code=404, detail="No path found between sections")

    result_dict = result.to_dict()
    await db.log_path_decision(result_dict)
    return result_dict


@router.post("/trigger-nudge")
async def trigger_nudge(
    req: DensityUpdateRequest,
    nudge_engine: NudgeEngine = Depends(get_nudge_engine),
    db: DatabaseManager = Depends(get_db),
):
    """
    Manually trigger nudge evaluation for a section.
    Used by dashboard operators for manual override.
    """
    nudge_action = nudge_engine.evaluate(
        section_id=req.section_id,
        density_score=req.density_score,
        predicted_density=req.predicted_density_10min,
    )
    if not nudge_action:
        return {"status": "no_action", "reason": "Density below threshold or no relief section"}

    nudge_dict = nudge_action.to_dict()
    await db.log_nudge(nudge_dict)
    return {"status": "nudge_triggered", "action": nudge_dict}


@router.post("/nudge-feedback")
async def nudge_feedback(
    req: NudgeFeedbackRequest,
    nudge_engine: NudgeEngine = Depends(get_nudge_engine),
):
    """
    Accept RL training feedback: how well did a nudge work?
    Called by the camera engine after observing post-nudge density changes.
    """
    nudge_engine.record_reward(req.section_id, req.nudge_type, req.reward)
    return {"status": "feedback_recorded"}


@router.get("/density-summary")
async def density_summary():
    """Return current density snapshot for all sections (dashboard polling)."""
    return {
        "densities": dict(_density_state),   # return copy; caller cannot mutate internal state
        "timestamp": time.time(),
        "high_risk": [s for s, d in _density_state.items() if d >= 0.70],
    }
