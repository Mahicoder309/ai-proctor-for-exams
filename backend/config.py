"""
config.py
---------
Shared configuration constants for the proctoring system.
"""

# ── Violation thresholds ────────────────────────────────────────────────────
NO_FACE_THRESHOLD_SEC    = 1.5   # seconds without face before NO_FACE violation
MULTI_FACE_THRESHOLD_SEC = 1.0   # seconds with >1 face before MULTIPLE_FACES violation
HAND_FACE_THRESHOLD_SEC  = 2.0   # seconds hand-over-face before HAND_OVER_FACE violation

# Gestures that are flagged as suspicious when near the face
GESTURE_FLAG_GESTURES = {"fist"}

# ── Camera ─────────────────────────────────────────────────────────────────
DEFAULT_CAMERA_INDEX = 0
