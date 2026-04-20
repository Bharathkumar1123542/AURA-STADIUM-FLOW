"""
Tests – FastAPI Routes (Integration)
======================================
Integration tests using FastAPI's TestClient (synchronous HTTP client).
No live database or GCP credentials are required — dependencies are
overridden via conftest.py fixtures.

Coverage:
  GET  /health
  POST /api/density-update   – valid + invalid payloads
  GET  /api/density-summary
  GET  /api/reroute-path     – valid + invalid sections
  POST /api/trigger-nudge    – above/below threshold
  POST /api/nudge-feedback
"""

import pytest


# ══════════════════════════════════════════════════════════════════
# Health endpoint
# ══════════════════════════════════════════════════════════════════


class TestHealth:
    def test_health_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_response_body(self, client):
        resp = client.get("/health")
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["service"] == "aura-backend-core"


# ══════════════════════════════════════════════════════════════════
# POST /api/density-update
# ══════════════════════════════════════════════════════════════════


class TestDensityUpdate:
    VALID_PAYLOAD = {
        "section_id": "C",
        "density_score": 0.87,
        "raw_density": 0.85,
        "person_count": 174,
        "capacity": 200,
        "threshold_breached": True,
        "predicted_density_10min": 0.90,
    }

    def test_valid_density_update_returns_200(self, client):
        resp = client.post("/api/density-update", json=self.VALID_PAYLOAD)
        assert resp.status_code == 200

    def test_response_has_required_fields(self, client):
        resp = client.post("/api/density-update", json=self.VALID_PAYLOAD)
        data = resp.json()
        assert "status" in data
        assert "nudge_triggered" in data
        assert "predicted_congestion_10min" in data

    def test_nudge_triggered_when_density_high(self, client):
        """density_score = 0.87 is above the 0.70 threshold → nudge should fire."""
        resp = client.post("/api/density-update", json=self.VALID_PAYLOAD)
        data = resp.json()
        assert data["nudge_triggered"] is True
        assert data["nudge_action"] is not None

    def test_nudge_not_triggered_when_density_low(self, client):
        payload = {**self.VALID_PAYLOAD, "density_score": 0.30, "threshold_breached": False,
                   "predicted_density_10min": 0.35}
        resp = client.post("/api/density-update", json=payload)
        data = resp.json()
        assert data["nudge_triggered"] is False
        assert data["nudge_action"] is None

    def test_invalid_section_id_returns_422(self, client):
        payload = {**self.VALID_PAYLOAD, "section_id": "Z"}
        resp = client.post("/api/density-update", json=payload)
        assert resp.status_code == 422

    def test_density_score_out_of_range_returns_422(self, client):
        payload = {**self.VALID_PAYLOAD, "density_score": 1.5}
        resp = client.post("/api/density-update", json=payload)
        assert resp.status_code == 422

    def test_negative_density_score_returns_422(self, client):
        payload = {**self.VALID_PAYLOAD, "density_score": -0.1}
        resp = client.post("/api/density-update", json=payload)
        assert resp.status_code == 422

    def test_missing_required_field_returns_422(self, client):
        payload = {"section_id": "A"}  # missing density_score
        resp = client.post("/api/density-update", json=payload)
        assert resp.status_code == 422

    def test_section_id_normalised_to_uppercase(self, client):
        payload = {**self.VALID_PAYLOAD, "section_id": "c"}
        resp = client.post("/api/density-update", json=payload)
        # Should succeed — Pydantic validator uppercases the value
        assert resp.status_code == 200

    def test_predicted_congestion_matches_input(self, client):
        resp = client.post("/api/density-update", json=self.VALID_PAYLOAD)
        data = resp.json()
        assert data["predicted_congestion_10min"] == pytest.approx(0.90)


# ══════════════════════════════════════════════════════════════════
# GET /api/density-summary
# ══════════════════════════════════════════════════════════════════


class TestDensitySummary:
    def test_density_summary_returns_200(self, client):
        resp = client.get("/api/density-summary")
        assert resp.status_code == 200

    def test_density_summary_has_densities_key(self, client):
        resp = client.get("/api/density-summary")
        data = resp.json()
        assert "densities" in data

    def test_density_summary_has_high_risk_key(self, client):
        resp = client.get("/api/density-summary")
        data = resp.json()
        assert "high_risk" in data

    def test_density_summary_has_timestamp(self, client):
        resp = client.get("/api/density-summary")
        data = resp.json()
        assert "timestamp" in data
        assert data["timestamp"] > 0

    def test_high_risk_reflects_density_updates(self, client):
        """After posting a high-density update, that section appears in high_risk."""
        client.post("/api/density-update", json={
            "section_id": "E",
            "density_score": 0.85,
            "predicted_density_10min": 0.88,
        })
        resp = client.get("/api/density-summary")
        data = resp.json()
        assert "E" in data["high_risk"]


# ══════════════════════════════════════════════════════════════════
# GET /api/reroute-path
# ══════════════════════════════════════════════════════════════════


class TestReroutePath:
    def test_valid_reroute_returns_200(self, client):
        resp = client.get("/api/reroute-path", params={"start": "A", "goal": "D"})
        assert resp.status_code == 200

    def test_response_has_path_key(self, client):
        resp = client.get("/api/reroute-path", params={"start": "A", "goal": "D"})
        data = resp.json()
        assert "path" in data

    def test_path_starts_and_ends_correctly(self, client):
        resp = client.get("/api/reroute-path", params={"start": "B", "goal": "F"})
        data = resp.json()
        assert data["path"][0] == "B"
        assert data["path"][-1] == "F"

    def test_same_start_and_goal(self, client):
        resp = client.get("/api/reroute-path", params={"start": "C", "goal": "C"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["path"] == ["C"]

    def test_invalid_start_returns_400(self, client):
        resp = client.get("/api/reroute-path", params={"start": "Z", "goal": "A"})
        assert resp.status_code == 400

    def test_invalid_goal_returns_400(self, client):
        resp = client.get("/api/reroute-path", params={"start": "A", "goal": "X"})
        assert resp.status_code == 400

    def test_lowercase_sections_accepted(self, client):
        """The endpoint uppercases start/goal before validation."""
        resp = client.get("/api/reroute-path", params={"start": "a", "goal": "d"})
        assert resp.status_code == 200


# ══════════════════════════════════════════════════════════════════
# POST /api/trigger-nudge
# ══════════════════════════════════════════════════════════════════


class TestTriggerNudge:
    def test_nudge_triggered_above_threshold(self, client):
        resp = client.post("/api/trigger-nudge", json={
            "section_id": "C",
            "density_score": 0.80,
            "predicted_density_10min": 0.85,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "nudge_triggered"
        assert "action" in data

    def test_no_action_below_threshold(self, client):
        resp = client.post("/api/trigger-nudge", json={
            "section_id": "A",
            "density_score": 0.30,
            "predicted_density_10min": 0.35,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "no_action"

    def test_invalid_section_returns_422(self, client):
        resp = client.post("/api/trigger-nudge", json={
            "section_id": "Q",
            "density_score": 0.80,
            "predicted_density_10min": 0.85,
        })
        assert resp.status_code == 422


# ══════════════════════════════════════════════════════════════════
# POST /api/nudge-feedback
# ══════════════════════════════════════════════════════════════════


class TestNudgeFeedback:
    def test_valid_feedback_returns_200(self, client):
        resp = client.post("/api/nudge-feedback", json={
            "section_id": "B",
            "nudge_type": "discount",
            "reward": 0.85,
        })
        assert resp.status_code == 200

    def test_feedback_recorded_status(self, client):
        resp = client.post("/api/nudge-feedback", json={
            "section_id": "D",
            "nudge_type": "led_only",
            "reward": 0.50,
        })
        data = resp.json()
        assert data["status"] == "feedback_recorded"

    def test_reward_out_of_range_returns_422(self, client):
        resp = client.post("/api/nudge-feedback", json={
            "section_id": "A",
            "nudge_type": "discount",
            "reward": 1.5,
        })
        assert resp.status_code == 422

    def test_invalid_section_returns_422(self, client):
        resp = client.post("/api/nudge-feedback", json={
            "section_id": "G",
            "nudge_type": "notification",
            "reward": 0.7,
        })
        assert resp.status_code == 422
