"""
AURA Backend Core – Nudge Engine
==================================
Decision layer that converts density readings into actionable nudges.

Architecture:
  Rule Engine (deterministic, fast) → base decision
  RL Hook (stochastic, learnable)   → adjusts nudge intensity over time

WHY TWO LAYERS:
  Rule engine guarantees safety and predictability in production.
  RL hook learns which nudges actually change crowd behavior.
  Separating them means the RL can fail gracefully without breaking rules.

Output contract (NudgeAction):
  {
    "action_id":      "nudge-C-1712860000",
    "section_from":   "C",
    "section_to":     "D",
    "nudge_type":     "discount",
    "value":          "20% off at Section D Grill",
    "led_from_state": "RED",
    "led_to_state":   "GREEN",
    "reason":         "Section C density 87% ≥ 70% threshold",
    "rl_confidence":  0.82,
    "timestamp":      1712860000.0
  }
"""

import logging
import random
import time
import uuid
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data Contracts
# ---------------------------------------------------------------------------

@dataclass
class NudgeAction:
    action_id: str
    section_from: str
    section_to: str
    nudge_type: str          # "discount" | "notification" | "led_only"
    value: str               # human-readable incentive description
    led_from_state: str      # "RED"
    led_to_state: str        # "GREEN"
    reason: str              # explainability field  ← WHY we triggered
    rl_confidence: float     # 0-1 score from RL hook
    timestamp: float

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Routing Map (who relieves whom)
# ---------------------------------------------------------------------------

# Defines which section absorbs crowd from a congested section.
# Key = congested section, Value = list of relief sections (priority order).
RELIEF_MAP: Dict[str, List[str]] = {
    "A": ["D", "B"],
    "B": ["E", "D"],
    "C": ["D", "F"],    # e.g., if C ≥70% → incentivize D (or F as fallback)
    "D": ["B", "A"],
    "E": ["F", "A"],
    "F": ["D", "E"],
}

INCENTIVE_TEMPLATES: Dict[str, str] = {
    "A": "Free drink voucher at Section A Bar!",
    "B": "20% off West Stand Concessions!",
    "C": "10% off South Gate Merchandise!",
    "D": "FREE upgrade to East Terrace viewing lounge!",
    "E": "North Gate: Priority queue pass now active!",
    "F": "VIP Lounge entry for Section F visitors!",
}


# ---------------------------------------------------------------------------
# RL Hook (epsilon-greedy policy stub)
# ---------------------------------------------------------------------------

class RLNudgeOptimizer:
    """
    Reinforcement Learning hook for nudge intensity optimization.

    Implementation: Epsilon-greedy bandit over nudge types.
    WHY BANDIT: Simpler than full RL; no environment model needed.
    Learns: which nudge type (discount/notification/led_only) gets
            the highest crowd-redistribution reward.

    Production upgrade path:
    - Replace Q-table with PPO agent using crowd sensor feedback.
    - Reward = delta_density in target section after 5 min.
    """

    NUDGE_TYPES = ["discount", "notification", "led_only"]
    EPSILON      = 0.15    # 15% exploration; 85% exploitation
    LEARNING_RATE = 0.1   # Q-update step size; increase for faster adaptation

    def __init__(self):
        # Q-values per (section, nudge_type); initialized optimistically
        self._q: Dict[str, Dict[str, float]] = {}

    def select(self, section_id: str) -> "tuple[str, float]":
        """
        Select nudge type for a section.
        Returns (nudge_type, confidence).
        """
        if section_id not in self._q:
            self._q[section_id] = {t: 0.5 for t in self.NUDGE_TYPES}

        if random.random() < self.EPSILON:
            # Explore: random selection
            chosen = random.choice(self.NUDGE_TYPES)
            confidence = 0.5
        else:
            # Exploit: best known type
            q_vals = self._q[section_id]
            chosen = max(q_vals, key=q_vals.get)
            confidence = q_vals[chosen]

        return chosen, confidence

    def update(self, section_id: str, nudge_type: str, reward: float) -> None:
        """
        Update Q-value after observing a reward signal.
        reward should be in [0, 1]: proportion of crowd that moved.
        """
        if section_id not in self._q:
            self._q[section_id] = {t: 0.5 for t in self.NUDGE_TYPES}
        alpha = self.LEARNING_RATE
        old = self._q[section_id][nudge_type]
        self._q[section_id][nudge_type] = old + alpha * (reward - old)
        logger.debug("RL update: section=%s type=%s reward=%.2f new_q=%.3f",
                     section_id, nudge_type, reward, self._q[section_id][nudge_type])


# ---------------------------------------------------------------------------
# Rule Engine
# ---------------------------------------------------------------------------

class RuleEngine:
    """
    Deterministic rule layer.
    Rules are evaluated in order; first match wins.
    WHY ORDERED: Allows high-priority emergency rules to short-circuit.
    """

    THRESHOLD = 0.70

    def evaluate(
        self,
        section_id: str,
        density_score: float,
        all_densities: Dict[str, float],
    ) -> Optional[dict]:
        """
        Returns a rule-match dict or None if no rule fires.

        dict keys: section_from, section_to, reason
        """
        # Rule 1: Standard congestion rule (core requirement)
        if density_score >= self.THRESHOLD:
            relief_sections = RELIEF_MAP.get(section_id, [])
            # Pick least-congested relief section
            best_relief = self._pick_relief(relief_sections, all_densities)
            if best_relief:
                return {
                    "section_from": section_id,
                    "section_to": best_relief,
                    "reason": (
                        f"Section {section_id} density {density_score*100:.1f}% "
                        f"≥ {self.THRESHOLD*100:.0f}% threshold"
                    ),
                }

        # Rule 2: Predictive pre-emption (density ascending fast)
        # This is handled at the API layer via predicted_density field.
        return None

    def _pick_relief(
        self, candidates: List[str], all_densities: Dict[str, float]
    ) -> Optional[str]:
        """Return the candidate with the lowest current density."""
        available = [c for c in candidates if c in all_densities]
        if not available:
            return candidates[0] if candidates else None
        return min(available, key=lambda s: all_densities[s])


# ---------------------------------------------------------------------------
# Nudge Engine (composes Rule Engine + RL Hook)
# ---------------------------------------------------------------------------

class NudgeEngine:
    """
    Production nudge orchestrator.

    Call flow:
        evaluate() → RuleEngine → NudgeAction
                   → RLNudgeOptimizer.select() (nudge type)
                   → structured JSON output
    """

    def __init__(self):
        self._rules = RuleEngine()
        self._rl = RLNudgeOptimizer()
        # In-memory density map: section → latest density score
        self._latest_densities: Dict[str, float] = {}

    def update_density(self, section_id: str, density_score: float) -> None:
        """Keep an up-to-date view of all section densities."""
        self._latest_densities[section_id] = density_score

    def evaluate(
        self,
        section_id: str,
        density_score: float,
        predicted_density: float = 0.0,
    ) -> Optional[NudgeAction]:
        """
        Main entry: returns a NudgeAction if action is warranted, else None.
        Also handles predictive triggers (predicted ≥ threshold, actual < threshold).
        """
        self.update_density(section_id, density_score)

        # Check actual threshold first, then predictive threshold
        effective_density = density_score
        reason_prefix = ""
        if density_score < self._rules.THRESHOLD and predicted_density >= self._rules.THRESHOLD:
            # Predictive pre-emption
            effective_density = predicted_density
            reason_prefix = "[PREDICTIVE] "

        match = self._rules.evaluate(section_id, effective_density, self._latest_densities)
        if not match:
            return None

        nudge_type, confidence = self._rl.select(section_id)
        target = match["section_to"]
        incentive = INCENTIVE_TEMPLATES.get(target, f"Move to Section {target}")

        action = NudgeAction(
            action_id=f"nudge-{section_id}-{uuid.uuid4().hex[:12]}",
            section_from=match["section_from"],
            section_to=target,
            nudge_type=nudge_type,
            value=incentive,
            led_from_state="RED",
            led_to_state="GREEN",
            reason=reason_prefix + match["reason"],
            rl_confidence=round(confidence, 3),
            timestamp=time.time(),
        )
        logger.info("🎯 Nudge fired: %s → %s (%s)", section_id, target, nudge_type)
        return action

    def record_reward(self, section_id: str, nudge_type: str, reward: float) -> None:
        """Allow external feedback to train the RL optimizer."""
        self._rl.update(section_id, nudge_type, reward)
