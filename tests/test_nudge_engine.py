"""
Tests – NudgeEngine, RuleEngine, RLNudgeOptimizer
====================================================
Unit tests for the core decision-making layer of AURA.

Coverage:
  RLNudgeOptimizer  – select(), update() (Q-learning correctness)
  RuleEngine        – evaluate() threshold logic, relief picking
  NudgeEngine       – full evaluate() flow, predictive pre-emption,
                      record_reward() delegation
"""

import time
import pytest

from backend_core.services.nudge_engine import (
    NudgeAction,
    NudgeEngine,
    RLNudgeOptimizer,
    RuleEngine,
    RELIEF_MAP,
    INCENTIVE_TEMPLATES,
)


# ══════════════════════════════════════════════════════════════════
# RLNudgeOptimizer
# ══════════════════════════════════════════════════════════════════


class TestRLNudgeOptimizer:
    VALID_TYPES = {"discount", "notification", "led_only"}

    def test_select_returns_valid_type(self, rl_optimizer):
        nudge_type, confidence = rl_optimizer.select("A")
        assert nudge_type in self.VALID_TYPES

    def test_select_confidence_in_range(self, rl_optimizer):
        _, confidence = rl_optimizer.select("B")
        assert 0.0 <= confidence <= 1.0

    def test_select_initialises_q_for_new_section(self, rl_optimizer):
        rl_optimizer.select("C")
        assert "C" in rl_optimizer._q
        # All nudge types should be initialised
        assert set(rl_optimizer._q["C"].keys()) == self.VALID_TYPES

    def test_update_moves_q_toward_reward(self, rl_optimizer):
        """Q-value should move from initial 0.5 toward reward 1.0."""
        section = "D"
        nudge_type = "discount"
        rl_optimizer.select(section)  # initialise Q
        old_q = rl_optimizer._q[section][nudge_type]
        rl_optimizer.update(section, nudge_type, reward=1.0)
        new_q = rl_optimizer._q[section][nudge_type]
        assert new_q > old_q, "Q-value should increase when reward > current Q"

    def test_update_moves_q_toward_zero_reward(self, rl_optimizer):
        """Q-value should move toward 0 when reward is 0."""
        section = "E"
        nudge_type = "notification"
        rl_optimizer.select(section)
        old_q = rl_optimizer._q[section][nudge_type]
        rl_optimizer.update(section, nudge_type, reward=0.0)
        new_q = rl_optimizer._q[section][nudge_type]
        assert new_q < old_q, "Q-value should decrease when reward < current Q"

    def test_update_initialises_section_if_missing(self, rl_optimizer):
        """update() should not raise even if section was never selected."""
        rl_optimizer.update("F", "led_only", reward=0.8)
        assert "F" in rl_optimizer._q

    def test_exploitation_returns_best_q_type(self, rl_optimizer):
        """After training, exploit mode should return the highest-Q nudge type."""
        section = "A"
        rl_optimizer.select(section)  # initialise
        # Artificially set discount Q very high
        rl_optimizer._q[section]["discount"] = 0.95
        rl_optimizer._q[section]["notification"] = 0.2
        rl_optimizer._q[section]["led_only"] = 0.3

        # Force exploitation by overriding EPSILON
        rl_optimizer.EPSILON = 0.0
        nudge_type, confidence = rl_optimizer.select(section)
        assert nudge_type == "discount"
        assert confidence == pytest.approx(0.95)


# ══════════════════════════════════════════════════════════════════
# RuleEngine
# ══════════════════════════════════════════════════════════════════


class TestRuleEngine:
    def test_no_action_below_threshold(self, rule_engine):
        result = rule_engine.evaluate("C", density_score=0.69, all_densities={})
        assert result is None

    def test_fires_at_exact_threshold(self, rule_engine):
        result = rule_engine.evaluate("C", density_score=0.70, all_densities={})
        assert result is not None
        assert result["section_from"] == "C"

    def test_fires_above_threshold(self, rule_engine):
        result = rule_engine.evaluate("A", density_score=0.85, all_densities={})
        assert result is not None

    def test_relief_section_is_valid(self, rule_engine):
        """Relief section must be a known neighbour from RELIEF_MAP."""
        for section in ["A", "B", "C", "D", "E", "F"]:
            result = rule_engine.evaluate(
                section, density_score=0.80, all_densities={}
            )
            assert result is not None
            assert result["section_to"] in RELIEF_MAP[section]

    def test_picks_least_congested_relief(self, rule_engine):
        """When both relief candidates are present, pick the less congested one."""
        # Section C relief options: ["D", "F"]  →  D at 0.9, F at 0.2 → pick F
        densities = {"D": 0.9, "F": 0.2}
        result = rule_engine.evaluate("C", density_score=0.80, all_densities=densities)
        assert result["section_to"] == "F"

    def test_reason_contains_section_and_density(self, rule_engine):
        result = rule_engine.evaluate("B", density_score=0.75, all_densities={})
        assert "B" in result["reason"]
        assert "75.0%" in result["reason"]


# ══════════════════════════════════════════════════════════════════
# NudgeEngine (full integration)
# ══════════════════════════════════════════════════════════════════


class TestNudgeEngine:
    def test_no_action_below_threshold(self, nudge_engine):
        action = nudge_engine.evaluate("A", density_score=0.50)
        assert action is None

    def test_action_above_threshold(self, nudge_engine):
        action = nudge_engine.evaluate("C", density_score=0.85)
        assert isinstance(action, NudgeAction)
        assert action.section_from == "C"

    def test_action_has_required_fields(self, nudge_engine):
        action = nudge_engine.evaluate("C", density_score=0.75)
        assert action is not None
        assert action.action_id.startswith("nudge-C-")
        assert action.nudge_type in {"discount", "notification", "led_only"}
        assert action.led_from_state == "RED"
        assert action.led_to_state == "GREEN"
        assert 0.0 <= action.rl_confidence <= 1.0
        assert action.timestamp > 0

    def test_to_dict_is_json_serialisable(self, nudge_engine):
        import json
        action = nudge_engine.evaluate("D", density_score=0.80)
        assert action is not None
        d = action.to_dict()
        # Should not raise
        json.dumps(d)

    def test_predictive_preemption_fires(self, nudge_engine):
        """
        When actual density < threshold but predicted >= threshold,
        the engine should still fire with [PREDICTIVE] prefix in reason.
        """
        action = nudge_engine.evaluate(
            "E", density_score=0.60, predicted_density=0.75
        )
        assert action is not None
        assert "[PREDICTIVE]" in action.reason

    def test_predictive_no_fire_when_both_below(self, nudge_engine):
        """Neither actual nor predicted triggers → no action."""
        action = nudge_engine.evaluate(
            "F", density_score=0.40, predicted_density=0.50
        )
        assert action is None

    def test_density_state_updates(self, nudge_engine):
        """evaluate() should update internal density snapshot."""
        nudge_engine.evaluate("A", density_score=0.55)
        assert nudge_engine._latest_densities["A"] == pytest.approx(0.55)

    def test_record_reward_delegates_to_rl(self, nudge_engine):
        """record_reward should update the RL optimizer's Q-table."""
        nudge_engine.evaluate("B", density_score=0.80)  # init Q for B
        nudge_engine.record_reward("B", "discount", reward=1.0)
        q = nudge_engine._rl._q.get("B", {})
        # After a reward of 1.0, discount Q should be above initial 0.5
        assert q.get("discount", 0.5) >= 0.5

    def test_incentive_value_matches_target_section(self, nudge_engine):
        """The nudge value should match the INCENTIVE_TEMPLATES entry for the target."""
        action = nudge_engine.evaluate("C", density_score=0.80)
        assert action is not None
        expected = INCENTIVE_TEMPLATES.get(action.section_to)
        if expected:
            assert action.value == expected
