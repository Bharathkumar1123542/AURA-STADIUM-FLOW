"""
AURA Camera Engine – Models Package

Provides the mock YOLOv8 detector and any future model wrappers.
Real deployments swap MockYOLOv8Detector for ultralytics.YOLO.
"""

from .yolo_detector import MockYOLOv8Detector

__all__ = ["MockYOLOv8Detector"]
