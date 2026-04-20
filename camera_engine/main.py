"""
AURA Camera Engine – Main Entry Point
======================================
Runs a continuous capture loop over all stadium sections,
publishing density readings to the backend via HTTP.

Usage:
    python -m camera_engine.main          # normal
    python -m camera_engine.main --surge C   # inject surge in section C
"""

import argparse
import json
import logging
import sys
import time
from typing import Optional

import requests

from camera_engine.models import MockYOLOv8Detector
from camera_engine.processors.density_analyzer import DensityAnalyzer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
)
logger = logging.getLogger("camera_engine.main")

BACKEND_URL = "http://backend_core:8000"   # Docker service name; use localhost locally
SECTIONS = ["A", "B", "C", "D", "E", "F"]
FRAME_INTERVAL_SEC = 1.0   # 1 Hz – balances latency vs compute


def build_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="AURA Camera Engine")
    p.add_argument("--surge", metavar="SECTION", default=None,
                   help="Inject crowd surge into a specific section (for testing)")
    p.add_argument("--backend", default=BACKEND_URL,
                   help="Backend URL (default: %(default)s)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print readings but do NOT POST to backend")
    return p.parse_args()


def post_density(reading_dict: dict, backend_url: str, dry_run: bool) -> None:
    """POST density reading to backend; swallows network errors gracefully."""
    if dry_run:
        logger.info("DRY-RUN | %s", json.dumps(reading_dict))
        return
    try:
        resp = requests.post(
            f"{backend_url}/density-update",
            json=reading_dict,
            timeout=2.0,
        )
        resp.raise_for_status()
        logger.debug("Posted section %s → %s", reading_dict["section_id"], resp.status_code)
    except requests.RequestException as exc:
        # Non-fatal: camera engine keeps running even if backend is down
        logger.warning("Backend unreachable: %s", exc)


def run(surge_section: Optional[str], backend_url: str, dry_run: bool) -> None:
    """Main capture loop."""
    detector = MockYOLOv8Detector(surge_section=surge_section)
    analyzer = DensityAnalyzer()

    logger.info("🎥 Camera engine started. Surge: %s | Backend: %s", surge_section, backend_url)

    try:
        while True:
            for section in SECTIONS:
                detection = detector.detect(section)
                reading = analyzer.analyze(detection)

                # Also attach 10-step prediction for backend decision layer
                prediction = analyzer.predict_congestion(section, horizon_steps=10)
                payload = reading.to_dict()
                payload["predicted_density_10min"] = prediction

                post_density(payload, backend_url, dry_run)
                time.sleep(FRAME_INTERVAL_SEC)   # 1s per section = 6s full cycle

    except KeyboardInterrupt:
        logger.info("Camera engine stopped.")
        sys.exit(0)


if __name__ == "__main__":
    args = build_args()
    run(
        surge_section=args.surge,
        backend_url=args.backend,
        dry_run=args.dry_run,
    )
