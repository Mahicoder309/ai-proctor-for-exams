"""
gesture.py
----------
Classifies hand gestures from MediaPipe hand landmarks.

Gesture classes:
  - "no hands"  : no landmarks detected
  - "fist"      : all (or most) fingers curled toward palm
  - "hand"      : open / partially open hand
  - "pointing"  : index finger extended, others curled
"""

from __future__ import annotations
from typing import Optional


# MediaPipe landmark indices
FINGERTIP_IDS  = [8, 12, 16, 20]   # index, middle, ring, pinky tips
FINGER_PIP_IDS = [6, 10, 14, 18]   # corresponding proximal interphalangeal joints
THUMB_TIP      = 4
THUMB_IP       = 3


def classify_gesture(hand_landmarks) -> str:
    """
    Classify a detected hand gesture.

    Parameters
    ----------
    hand_landmarks : mediapipe.framework.formats.landmark_pb2.NormalizedLandmarkList | None
        The 21-landmark result from MediaPipe Hands for ONE hand.
        Pass None (or call with no landmarks) to get "no hands".

    Returns
    -------
    str
        One of: "no hands", "fist", "pointing", "hand"
    """
    if hand_landmarks is None:
        return "no hands"

    lm = hand_landmarks.landmark

    # Count curled fingers (tip y > pip y means tip is below the knuckle → curled)
    curled = 0
    for tip_id, pip_id in zip(FINGERTIP_IDS, FINGER_PIP_IDS):
        if lm[tip_id].y > lm[pip_id].y:
            curled += 1

    # Thumb curl: compare tip x to IP joint x (handedness-agnostic: use distance)
    thumb_curled = abs(lm[THUMB_TIP].x - lm[0].x) < abs(lm[THUMB_IP].x - lm[0].x)

    # Pointing: index extended (not curled), rest curled
    index_extended = lm[8].y < lm[6].y
    others_curled  = (lm[12].y > lm[10].y and
                      lm[16].y > lm[14].y and
                      lm[20].y > lm[18].y)
    if index_extended and others_curled:
        return "pointing"

    if curled >= 3:
        return "fist"

    return "hand"
