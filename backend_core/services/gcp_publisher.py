"""
AURA Backend Core – Google Cloud Pub/Sub Publisher
====================================================
Publishes nudge and density events to GCP Pub/Sub topics for
real-time downstream consumers (analytics pipeline, fan app push, etc.).

Topics:
  aura-nudge-events      – every NudgeAction triggered
  aura-density-updates   – every density reading received

Design decisions:
  - Graceful no-op fallback when GCP_PROJECT_ID is unset or the SDK is
    unavailable. This mirrors the DatabaseManager fallback pattern so
    the system runs identically in local/test and cloud environments.
  - Messages are published synchronously (via to_thread in routes) to
    avoid blocking the async event loop.
  - JSON serialisation converts float timestamps to ISO strings so
    downstream consumers don't need to know Python's epoch format.

Usage (environment variables):
  GCP_PROJECT_ID   – GCP project identifier (required for live mode)
  PUBSUB_EMULATOR_HOST – optional, points to a local Pub/Sub emulator
"""

import json
import logging
import os
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional SDK import
# ---------------------------------------------------------------------------
try:
    from google.cloud import pubsub_v1  # type: ignore

    _PUBSUB_AVAILABLE = True
except ImportError:
    _PUBSUB_AVAILABLE = False
    logger.warning(
        "google-cloud-pubsub not installed; Pub/Sub publishing disabled. "
        "Install with: pip install google-cloud-pubsub"
    )


# ---------------------------------------------------------------------------
# Publisher
# ---------------------------------------------------------------------------


class GCPPublisher:
    """
    Thin wrapper around the Pub/Sub PublisherClient.

    Call flow:
        publish_nudge(nudge_dict)    → aura-nudge-events topic
        publish_density(density_dict) → aura-density-updates topic

    Both methods return immediately with no error if GCP is unavailable.
    """

    TOPIC_NUDGE = "aura-nudge-events"
    TOPIC_DENSITY = "aura-density-updates"

    def __init__(self) -> None:
        self._project_id: Optional[str] = os.environ.get("GCP_PROJECT_ID")
        self._client: Any = None
        self._enabled: bool = False

        if not _PUBSUB_AVAILABLE:
            logger.info("GCPPublisher: SDK unavailable, running in no-op mode.")
            return

        if not self._project_id:
            logger.info(
                "GCPPublisher: GCP_PROJECT_ID not set, running in no-op mode. "
                "Set this env var to enable Pub/Sub publishing."
            )
            return

        try:
            self._client = pubsub_v1.PublisherClient()
            self._enabled = True
            logger.info(
                "✅ GCPPublisher initialised for project '%s'.", self._project_id
            )
        except Exception as exc:
            logger.error("GCPPublisher init failed (no-op): %s", exc)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def publish_nudge(self, nudge_dict: Dict[str, Any]) -> None:
        """
        Publish a NudgeAction to the aura-nudge-events Pub/Sub topic.

        Args:
            nudge_dict: Serialised NudgeAction (from NudgeAction.to_dict()).
        """
        self._publish(self.TOPIC_NUDGE, nudge_dict, event_type="nudge_fired")

    def publish_density(self, density_dict: Dict[str, Any]) -> None:
        """
        Publish a density reading to the aura-density-updates Pub/Sub topic.

        Args:
            density_dict: Serialised DensityUpdateRequest payload.
        """
        self._publish(self.TOPIC_DENSITY, density_dict, event_type="density_update")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _publish(
        self,
        topic_id: str,
        payload: Dict[str, Any],
        event_type: str,
    ) -> None:
        """Serialise payload to JSON and publish to the given topic."""
        if not self._enabled or self._client is None:
            logger.debug(
                "GCPPublisher (no-op): would publish '%s' to topic '%s'.",
                event_type,
                topic_id,
            )
            return

        topic_path = self._client.topic_path(self._project_id, topic_id)
        message = {
            "event_type": event_type,
            "published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "data": payload,
        }
        data = json.dumps(message, default=str).encode("utf-8")

        try:
            future = self._client.publish(topic_path, data=data)
            message_id = future.result(timeout=5)
            logger.debug(
                "Pub/Sub published '%s' → topic '%s' (message_id=%s).",
                event_type,
                topic_id,
                message_id,
            )
        except Exception as exc:
            # Non-fatal: log and continue; never raise to caller
            logger.error(
                "Pub/Sub publish failed for topic '%s': %s", topic_id, exc
            )

    @property
    def enabled(self) -> bool:
        """True when connected to a live GCP Pub/Sub project."""
        return self._enabled
