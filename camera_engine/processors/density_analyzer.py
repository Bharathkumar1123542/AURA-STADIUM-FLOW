"""
AURA Camera Engine – Density Analyzer
======================================
FORMULA:
    raw_density  = detected_persons / zone_capacity
    smooth_density = α * raw_density + (1-α) * prev_smooth   [EMA]

WHY EMA: Exponential Moving Average dampens single-frame spikes caused
by partial occlusions or lighting flicker, preventing false positives
from triggering downstream nudge actions.

Output schema (JSON):
    {
        "section_id": "C",
        "density_score": 0.87,          # 0.0–1.0
        "raw_density":   0.91,
        "person_count":  182,
        "capacity":      200,
        "timestamp":     1712860000.0,
        "threshold_breached": true
    }
"""

import time
import logging
from collections import defaultdict, deque
from dataclasses import dataclass, asdict
from typing import Callable, Dict, List, Optional



logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data Contracts
# ---------------------------------------------------------------------------

@dataclass
class DensityReading:
    section_id: str
    density_score: float          # EMA-smoothed (0-1)
    raw_density: float            # Instantaneous (0-1)
    person_count: int
    capacity: int
    timestamp: float
    threshold_breached: bool

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Core Analyzer
# ---------------------------------------------------------------------------

class DensityAnalyzer:
    """
    Converts raw YOLO detection results into actionable density readings.

    Responsibilities:
    - Maintain per-section capacity map
    - Apply EMA smoothing
    - Maintain short-term history for prediction (5-min rolling window)
    - Fire registered callbacks when threshold is breached
    """

    # Stadium section capacities (persons). Configurable via constructor.
    DEFAULT_CAPACITIES: Dict[str, int] = {
        "A": 200, "B": 150, "C": 220,
        "D": 180, "E": 200, "F": 160,
    }

    THRESHOLD: float = 0.70            # 70% density → congested
    EMA_ALPHA: float = 0.35            # smoothing factor (higher = faster response)
    HISTORY_WINDOW: int = 60           # keep last 60 readings (~5 min at 1 Hz)

    def __init__(
        self,
        capacities: Optional[Dict[str, int]] = None,
        threshold: float = THRESHOLD,
        ema_alpha: float = EMA_ALPHA,
    ):
        self._capacities = capacities or self.DEFAULT_CAPACITIES
        self._threshold = threshold
        self._alpha = ema_alpha

        # EMA state per section
        self._ema: Dict[str, float] = defaultdict(float)
        # Rolling history for prediction
        self._history: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=self.HISTORY_WINDOW)
        )
        # Registered callbacks for threshold events
        self._callbacks: List[Callable[[DensityReading], None]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register_callback(self, fn: Callable[[DensityReading], None]) -> None:
        """Register a function to be called when density threshold is crossed."""
        self._callbacks.append(fn)

    def analyze(self, detection_result: "DetectionResult") -> DensityReading:
        """
        Main entry point: consume a DetectionResult, return a DensityReading.
        Side effect: fires callbacks if threshold crossed.
        """
        sid = detection_result.section_id
        capacity = self._capacities.get(sid, 200)
        person_count = len(detection_result.boxes)

        raw = min(person_count / capacity, 1.0)          # clamp to [0,1]
        smoothed = self._update_ema(sid, raw)
        breached = smoothed >= self._threshold

        reading = DensityReading(
            section_id=sid,
            density_score=round(smoothed, 4),
            raw_density=round(raw, 4),
            person_count=person_count,
            capacity=capacity,
            timestamp=detection_result.timestamp,
            threshold_breached=breached,
        )

        # Store for prediction
        self._history[sid].append(reading)

        if breached:
            logger.warning(
                "⚠️  Section %s density=%.1f%% – threshold breached!",
                sid, smoothed * 100
            )
            for cb in self._callbacks:
                try:
                    cb(reading)
                except Exception as exc:
                    logger.error("Callback error: %s", exc)

        return reading

    def predict_congestion(self, section_id: str, horizon_steps: int = 10) -> float:
        """
        Predict density `horizon_steps` readings into the future.

        Method: Linear regression on slope of last N EMA values.
        WHY LINEAR: At short horizons (5-10 min) crowd movement is
        approximately linear; non-linear models require training data.

        Returns:
            Predicted density score (0–1), clamped.
        """
        history = list(self._history[section_id])
        if len(history) < 3:
            return self._ema.get(section_id, 0.0)

        scores = [r.density_score for r in history[-20:]]  # last 20 pts
        n = len(scores)
        # Simple linear regression slope
        x_mean = (n - 1) / 2
        y_mean = sum(scores) / n
        numerator = sum((i - x_mean) * (s - y_mean) for i, s in enumerate(scores))
        denominator = sum((i - x_mean) ** 2 for i in range(n)) or 1e-9
        slope = numerator / denominator

        predicted = scores[-1] + slope * horizon_steps
        return round(max(0.0, min(1.0, predicted)), 4)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _update_ema(self, section_id: str, raw: float) -> float:
        """Update and return the EMA for a section."""
        prev = self._ema[section_id]
        new_ema = self._alpha * raw + (1 - self._alpha) * prev
        self._ema[section_id] = new_ema
        return new_ema
