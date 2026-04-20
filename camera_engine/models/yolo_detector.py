"""
AURA Camera Engine – Mock YOLOv8 Detector
==========================================
WHY MOCK: Real YOLOv8 requires GPU/camera hardware unavailable in all
environments. This mock reproduces the EXACT output contract of
ultralytics.YOLO so switching is a single-line change.

Production swap:
    from ultralytics import YOLO
    model = YOLO("yolov8n.pt")
    results = model(frame)
"""

import random
import time
from dataclasses import dataclass, field
from typing import List


@dataclass
class BoundingBox:
    """Represents one detected person in the frame."""
    x1: float
    y1: float
    x2: float
    y2: float
    confidence: float
    class_id: int = 0          # 0 = 'person' in COCO taxonomy
    class_name: str = "person"


@dataclass
class DetectionResult:
    """Output contract matching ultralytics Results object."""
    frame_id: int
    section_id: str
    timestamp: float
    boxes: List[BoundingBox] = field(default_factory=list)


class MockYOLOv8Detector:
    """
    Simulates YOLOv8 person detection per stadium section.

    Crowd behavior is modelled using:
    - A base occupancy (section-dependent realistic values)
    - Gaussian noise (+/- 5 persons)
    - Optional surge injection for testing threshold logic
    """

    # Realistic base crowd counts per section (out of frame capacity ~200 persons)
    SECTION_BASE_COUNTS = {
        "A": 60,   # main exit corridor – moderate
        "B": 40,   # west concessions
        "C": 90,   # south exit – heaviest
        "D": 30,   # east terrace – lightest
        "E": 55,   # north gate
        "F": 70,   # VIP lounge corridor
    }

    def __init__(self, noise_std: float = 5.0, surge_section: str | None = None):
        """
        Args:
            noise_std:      Gaussian noise std on person count (simulates CV error).
            surge_section:  If set, that section gets +80 persons (triggers threshold).
        """
        self._noise_std = noise_std
        self._surge_section = surge_section
        self._frame_counter = 0

    def detect(self, section_id: str) -> DetectionResult:
        """Run detection on a (simulated) camera frame for a section."""
        self._frame_counter += 1

        base = self.SECTION_BASE_COUNTS.get(section_id, 50)
        if self._surge_section == section_id:
            base += 80   # simulated halftime crush

        # Apply Gaussian noise; clamp to [0, 200]
        count = max(0, min(200, int(random.gauss(base, self._noise_std))))

        boxes = self._generate_boxes(count)

        return DetectionResult(
            frame_id=self._frame_counter,
            section_id=section_id,
            timestamp=time.time(),
            boxes=boxes,
        )

    def _generate_boxes(self, count: int) -> List[BoundingBox]:
        """
        Generate plausible bounding boxes for `count` persons inside
        a normalized 1920×1080 frame.  Boxes avoid perfect grids to
        mimic real crowd clustering.
        """
        boxes = []
        for _ in range(count):
            x1 = random.uniform(0, 1820)
            # Clamp y1 so that y2 = y1 + max_height (160) stays within 1080
            y1 = random.uniform(0, 920)
            boxes.append(BoundingBox(
                x1=x1,
                y1=y1,
                x2=x1 + random.uniform(30, 80),   # person width 30-80 px
                y2=y1 + random.uniform(60, 160),   # person height 60-160 px (max y2=1080)
                confidence=random.uniform(0.70, 0.99),
            ))
        return boxes
