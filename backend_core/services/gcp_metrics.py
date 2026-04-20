"""
AURA Backend Core – Google Cloud Monitoring Custom Metrics
===========================================================
Reports crowd density gauge values as custom metrics to Cloud Monitoring.

Metric descriptor:
  custom.googleapis.com/aura/crowd_density
  - type: GAUGE
  - valueType: DOUBLE
  - unit: 1 (proportion 0.0–1.0)
  - labels: section_id (STRING)

Design decisions:
  - Follows the same no-op fallback pattern as GCPPublisher.
  - Metric writes are best-effort; failure never propagates to callers.
  - One MetricServiceClient is created at startup and reused (thread-safe).
  - Uses google-cloud-monitoring ≥ 2.x API (MetricServiceClient).

Usage (environment variables):
  GCP_PROJECT_ID   – GCP project identifier (required for live mode)
"""

import logging
import os
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional SDK import
# ---------------------------------------------------------------------------
try:
    from google.cloud import monitoring_v3  # type: ignore

    _MONITORING_AVAILABLE = True
except ImportError:
    _MONITORING_AVAILABLE = False
    logger.warning(
        "google-cloud-monitoring not installed; Cloud Monitoring disabled. "
        "Install with: pip install google-cloud-monitoring"
    )


class GCPMetricsReporter:
    """
    Reports crowd density values as Cloud Monitoring custom metrics.

    Example usage:
        reporter = GCPMetricsReporter()
        reporter.report_density("C", 0.87)

    If GCP is unavailable the call returns silently in under 1 µs.
    """

    METRIC_TYPE = "custom.googleapis.com/aura/crowd_density"

    def __init__(self) -> None:
        self._project_id: Optional[str] = os.environ.get("GCP_PROJECT_ID")
        self._client: Any = None
        self._enabled: bool = False

        if not _MONITORING_AVAILABLE:
            logger.info("GCPMetricsReporter: SDK unavailable, running in no-op mode.")
            return

        if not self._project_id:
            logger.info(
                "GCPMetricsReporter: GCP_PROJECT_ID not set, running in no-op mode."
            )
            return

        try:
            self._client = monitoring_v3.MetricServiceClient()
            self._enabled = True
            logger.info(
                "✅ GCPMetricsReporter initialised for project '%s'.",
                self._project_id,
            )
        except Exception as exc:
            logger.error("GCPMetricsReporter init failed (no-op): %s", exc)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def report_density(self, section_id: str, density_score: float) -> None:
        """
        Write a crowd density data point for a specific stadium section.

        Args:
            section_id:     Stadium section identifier (e.g., "C").
            density_score:  Normalised density value in [0.0, 1.0].
        """
        if not self._enabled or self._client is None:
            logger.debug(
                "GCPMetricsReporter (no-op): section=%s density=%.3f",
                section_id,
                density_score,
            )
            return

        try:
            series = monitoring_v3.TimeSeries()
            series.metric.type = self.METRIC_TYPE
            series.metric.labels["section_id"] = section_id
            series.resource.type = "global"

            now = time.time()
            seconds = int(now)
            nanos = int((now - seconds) * 10**9)

            interval = monitoring_v3.TimeInterval(
                {"end_time": {"seconds": seconds, "nanos": nanos}}
            )
            point = monitoring_v3.Point(
                {"interval": interval, "value": {"double_value": density_score}}
            )
            series.points = [point]

            project_name = f"projects/{self._project_id}"
            self._client.create_time_series(
                name=project_name, time_series=[series]
            )
            logger.debug(
                "Cloud Monitoring: wrote density %.3f for section %s.",
                density_score,
                section_id,
            )
        except Exception as exc:
            # Non-fatal: monitoring failure must never impact request handling
            logger.error(
                "Cloud Monitoring write failed for section '%s': %s",
                section_id,
                exc,
            )

    @property
    def enabled(self) -> bool:
        """True when connected to a live Cloud Monitoring project."""
        return self._enabled
