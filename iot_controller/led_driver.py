"""
AURA IoT Controller – LED Driver
===================================
Translates backend nudge actions into physical LED zone commands.

LED States:
  GREEN  → safe, optimal path, fan should move here
  RED    → congested, avoid
  AMBER  → approaching threshold (predictive warning)
  WHITE  → idle / normal

State machine:
  IDLE ──[density ≥ 70%]──→ RED
  RED  ──[nudge fired]   ──→ target section = GREEN
  GREEN──[density < 30%] ──→ IDLE

WHY STATE MACHINE:
  Prevents rapid LED flicker when density oscillates near threshold.
  A state change requires crossing a hysteresis band (60–75%).
"""

import logging
import time
from enum import Enum
from typing import Dict, Optional

from iot_controller.mqtt_client import AuraMQTTClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LED State Definitions
# ---------------------------------------------------------------------------

class LEDState(str, Enum):
    WHITE = "WHITE"    # idle / baseline
    AMBER = "AMBER"    # approaching threshold (55-70%)
    RED   = "RED"      # congested (≥ 70%)
    GREEN = "GREEN"    # reroute target (optimal path)


# Density thresholds for state transitions (with hysteresis)
THRESHOLD_RED   = 0.70    # enter RED
THRESHOLD_AMBER = 0.55    # enter AMBER
THRESHOLD_IDLE  = 0.30    # return to WHITE from RED/AMBER


# ---------------------------------------------------------------------------
# LED Zone Controller
# ---------------------------------------------------------------------------

class LEDZoneController:
    """
    Manages LED state per stadium zone.

    Receives:
    - Density updates → automatic state computation
    - Nudge actions   → explicit LED override (target section = GREEN)

    Publishes state changes via MQTT.
    """

    def __init__(self, mqtt_client: Optional[AuraMQTTClient] = None):
        self._mqtt = mqtt_client or AuraMQTTClient()
        self._states: Dict[str, LEDState] = {}  # section → current LED state
        self._override_expires: Dict[str, float] = {}  # section → override expiry time

    def connect(self) -> None:
        """Connect MQTT client. Call once at startup."""
        self._mqtt.connect()

    def process_density(self, section_id: str, density_score: float) -> LEDState:
        """
        Compute and apply LED state based on current density.
        Respects active overrides (e.g., GREEN path from nudge).

        Returns the new LED state.
        """
        # Purge all expired overrides on every density cycle to prevent
        # unbounded growth of _override_expires for un-polled sections.
        self._purge_expired_overrides()
        # Don't override active nudge-driven GREEN signals
        if self._has_active_override(section_id):
            logger.debug("Section %s has active override – skipping density update", section_id)
            return self._states.get(section_id, LEDState.WHITE)

        new_state = self._compute_state(section_id, density_score)
        self._apply_state(section_id, new_state)
        return new_state

    def apply_nudge(self, nudge_action: dict) -> None:
        """
        Apply LED states from a nudge action:
          - section_from → RED  (confirm it's congested)
          - section_to   → GREEN (guide crowd here)

        GREEN override lasts 10 minutes to avoid immediate reversion.
        """
        sec_from = nudge_action.get("section_from")
        sec_to   = nudge_action.get("section_to")

        if sec_from:
            self._apply_state(sec_from, LEDState.RED)

        if sec_to:
            self._apply_state(sec_to, LEDState.GREEN)
            # Set override: GREEN persists for 600s regardless of density
            self._override_expires[sec_to] = time.time() + 600
            logger.info("🟢 Section %s GREEN override set for 10 min", sec_to)

        # Publish fan alert
        if sec_from and sec_to and self._mqtt:
            self._mqtt.publish_alert(
                section_id=sec_from,
                message=f"Section {sec_from} is full! Head to Section {sec_to} for a reward.",
                nudge_action=nudge_action,
            )

    def get_all_states(self) -> Dict[str, str]:
        """Return current LED states for all tracked sections."""
        return {s: v.value for s, v in self._states.items()}

    def disconnect(self) -> None:
        """Gracefully disconnect the MQTT client."""
        self._mqtt.disconnect()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_state(self, section_id: str, density: float) -> LEDState:
        """State machine with hysteresis to prevent flickering."""
        current = self._states.get(section_id, LEDState.WHITE)

        if density >= THRESHOLD_RED:
            return LEDState.RED
        elif density >= THRESHOLD_AMBER:
            return LEDState.AMBER
        elif density < THRESHOLD_IDLE and current in (LEDState.RED, LEDState.AMBER):
            return LEDState.WHITE
        else:
            # Hold current state (hysteresis band 30-55%)
            return current

    def _apply_state(self, section_id: str, state: LEDState) -> None:
        """Apply state change if different from current; publish via MQTT."""
        current = self._states.get(section_id)
        if current == state:
            return  # No change – don't flood MQTT

        self._states[section_id] = state
        self._mqtt.publish_led_command(
            section_id=section_id,
            state=state.value,
            metadata={"auto": current is not None},
        )
        logger.info("💡 Section %s: %s → %s", section_id, current, state)

    def _has_active_override(self, section_id: str) -> bool:
        """Check if a GREEN override is still active for a section."""
        expiry = self._override_expires.get(section_id, 0)
        return time.time() < expiry

    def _purge_expired_overrides(self) -> None:
        """Remove all expired override entries in a single pass.
        Called from process_density() so stale keys don't accumulate for
        sections that are never polled individually.
        """
        now = time.time()
        expired = [s for s, exp in self._override_expires.items() if now >= exp]
        for s in expired:
            del self._override_expires[s]


# ---------------------------------------------------------------------------
# Standalone runner (for Docker container)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json, sys

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s – %(message)s")

    controller = LEDZoneController()
    controller.connect()

    logger.info("🔆 LED Driver running. Waiting for density events...")
    # In production this would subscribe to Redis/NATS for density events.
    # Here we simulate a loop.
    import random
    sections = ["A", "B", "C", "D", "E", "F"]
    try:
        while True:
            for sec in sections:
                density = random.uniform(0.2, 0.95)
                state = controller.process_density(sec, density)
            time.sleep(2)
    except KeyboardInterrupt:
        controller.disconnect()   # public API – no private attribute access
        sys.exit(0)
