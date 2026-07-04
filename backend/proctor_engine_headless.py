"""
proctor_engine_headless.py
--------------------------
Headless (no PyQt5) version of the proctoring CV pipeline.

Instead of QThread signals, it uses:
  - asyncio.Queue for sending frames + state back to the WebSocket handler
  - threading.Thread for the blocking CV loop
"""

from __future__ import annotations

import asyncio
import base64
import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision

from config import (
    NO_FACE_THRESHOLD_SEC,
    MULTI_FACE_THRESHOLD_SEC,
    HAND_FACE_THRESHOLD_SEC,
    GESTURE_FLAG_GESTURES,
)
from gesture import classify_gesture
from object_detector import CheatingObjectDetector, draw_detections
from violation_logger import (
    ViolationLogger,
    VT_NO_FACE, VT_MULTI_FACE, VT_HAND_OVER_FACE,
    VT_SUSPICIOUS_GESTURE, VT_PHONE_DETECTED,
    VT_CHEATING_OBJECT,
)

# ── Model paths ────────────────────────────────────────────────────────────
_BASE      = Path(__file__).parent / "models"
FACE_MODEL = str(_BASE / "face_detector.tflite")
HAND_MODEL = str(_BASE / "hand_landmarker.task")

# ── Object violation thresholds ────────────────────────────────────────────
PHONE_THRESHOLD_SEC  = 0.2
OBJECT_THRESHOLD_SEC = 0.3
OBJECT_COOLDOWN_SEC  = 8.0

# ── Colors (BGR) ──────────────────────────────────────────────────────────
COLOR_GREEN  = (0,   210,  80)
COLOR_RED    = (0,    40, 220)
COLOR_ORANGE = (0,   150, 240)
COLOR_WHITE  = (255, 255, 255)
COLOR_BLACK  = (0,     0,   0)


class HeadlessProctorEngine:
    """
    Headless proctoring engine.  Runs CV loop in a background thread.
    Decoded JPEG frames are decoded from bytes (sent by browser via WebSocket).

    Callbacks (called from the CV thread — use asyncio.run_coroutine_threadsafe):
      on_state(state: dict)
      on_violation(event: dict)
      on_annotated_frame(jpeg_b64: str)
    """

    def __init__(
        self,
        student_id: str,
        loop: asyncio.AbstractEventLoop,
        queue: asyncio.Queue,
    ) -> None:
        self.student_id = student_id
        self._loop      = loop
        self._queue     = queue   # outgoing messages → WebSocket sender
        self._running   = False
        self._thread: Optional[threading.Thread] = None
        self.logger: Optional[ViolationLogger] = None

        # incoming frame queue (bytes from browser)
        self._frame_queue: "asyncio.Queue[Optional[bytes]]" = asyncio.Queue(maxsize=5)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._running = True
        self.logger   = ViolationLogger(self.student_id)
        self._thread  = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> Optional[dict]:
        self._running = False
        # unblock the frame queue
        asyncio.run_coroutine_threadsafe(
            self._frame_queue.put(None), self._loop
        )
        if self._thread:
            self._thread.join(timeout=5)
        if self.logger:
            return self.logger.end_session()
        return None

    def push_frame(self, jpeg_bytes: bytes) -> None:
        """Called from async WebSocket handler to push a frame into the CV thread."""
        if self._frame_queue.full():
            try:
                self._frame_queue.get_nowait()   # drop oldest
            except Exception:
                pass
        asyncio.run_coroutine_threadsafe(
            self._frame_queue.put(jpeg_bytes), self._loop
        )

    # ------------------------------------------------------------------
    # Internal CV loop (runs in background thread)
    # ------------------------------------------------------------------

    def _run(self) -> None:
        face_opts = mp_vision.FaceDetectorOptions(
            base_options=mp_python.BaseOptions(model_asset_path=FACE_MODEL),
            running_mode=mp_vision.RunningMode.IMAGE,
            min_detection_confidence=0.6,
        )
        hand_opts = mp_vision.HandLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=HAND_MODEL),
            running_mode=mp_vision.RunningMode.IMAGE,
            num_hands=2,
            min_hand_detection_confidence=0.6,
            min_tracking_confidence=0.5,
        )

        face_detector = mp_vision.FaceDetector.create_from_options(face_opts)
        hand_tracker  = mp_vision.HandLandmarker.create_from_options(hand_opts)
        obj_detector  = CheatingObjectDetector()

        YOLO_FRAME_SKIP = 3
        frame_idx = 0

        no_face_timer = multi_face_timer = hand_face_timer = 0.0
        no_face_flagged = multi_face_flagged = hand_face_flagged = False

        phone_timer = object_timer = 0.0
        phone_flagged = object_flagged = False
        last_object_label  = ""
        object_cooldown_ts: dict[str, float] = {}
        last_detections    = []
        phone_frames_since_detect  = 99
        object_frames_since_detect = 99

        prev_time = time.time()

        while self._running:
            # ── Block until a frame arrives ────────────────────────────
            future = asyncio.run_coroutine_threadsafe(
                self._frame_queue.get(), self._loop
            )
            try:
                jpeg_bytes = future.result(timeout=2.0)
            except Exception:
                continue

            if jpeg_bytes is None:   # stop signal
                break

            # Decode JPEG
            nparr = np.frombuffer(jpeg_bytes, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if frame is None:
                continue

            now      = time.time()
            dt       = now - prev_time
            prev_time = now
            frame_idx += 1

            h, w = frame.shape[:2]
            rgb      = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

            # ── Face detection ────────────────────────────────────────
            face_result = face_detector.detect(mp_image)
            faces       = face_result.detections if face_result.detections else []
            face_count  = len(faces)

            face_bboxes = []
            for det in faces:
                bbox = det.bounding_box
                x1 = bbox.origin_x; y1 = bbox.origin_y
                x2 = x1 + bbox.width; y2 = y1 + bbox.height
                face_bboxes.append((x1, y1, x2, y2))
                cv2.rectangle(frame, (x1, y1), (x2, y2), COLOR_GREEN, 2)

            # ── Hand detection ────────────────────────────────────────
            hand_result    = hand_tracker.detect(mp_image)
            gestures:      list[str] = []
            hand_near_face = False

            if hand_result.hand_landmarks:
                for hand_lm_list in hand_result.hand_landmarks:
                    _draw_hand_skeleton(frame, hand_lm_list, w, h)
                    lm_wrapper = _LandmarkWrapper(hand_lm_list)
                    g = classify_gesture(lm_wrapper)
                    gestures.append(g)

                    for x1, y1, x2, y2 in face_bboxes:
                        wrist = hand_lm_list[0]
                        wx = int(wrist.x * w); wy = int(wrist.y * h)
                        mx = int((x2 - x1) * 0.15); my = int((y2 - y1) * 0.15)
                        if (x1 - mx <= wx <= x2 + mx and y1 - my <= wy <= y2 + my):
                            hand_near_face = True

            gesture_label = gestures[0] if gestures else "no hands"

            # ── YOLO object detection ─────────────────────────────────
            if frame_idx % YOLO_FRAME_SKIP == 0:
                last_detections = obj_detector.detect(frame)

            draw_detections(frame, last_detections)
            yolo_status = obj_detector.model_status

            raw_phone_visible  = any(d.is_phone for d in last_detections)
            raw_object_visible = bool(last_detections) and not raw_phone_visible

            if raw_phone_visible:
                phone_frames_since_detect = 0
            else:
                phone_frames_since_detect += 1

            if raw_object_visible:
                object_frames_since_detect = 0
                last_object_label = last_detections[0].label
            else:
                object_frames_since_detect += 1

            phone_visible  = phone_frames_since_detect  <= 6
            object_visible = object_frames_since_detect <= 6
            object_label   = last_object_label if object_visible else ""

            # ── State machine ─────────────────────────────────────────
            def _emit_violation(vtype, dur=0.0):
                ev = self.logger.log_violation(vtype, frame, dur)
                self._send({"type": "violation", "event": ev})

            if face_count == 0:
                no_face_timer += dt
                multi_face_timer = 0.0; multi_face_flagged = False
                if no_face_timer >= NO_FACE_THRESHOLD_SEC and not no_face_flagged:
                    _emit_violation(VT_NO_FACE, no_face_timer)
                    no_face_flagged = True
            elif face_count > 1:
                no_face_timer = 0.0; no_face_flagged = False
                multi_face_timer += dt
                if multi_face_timer >= MULTI_FACE_THRESHOLD_SEC and not multi_face_flagged:
                    _emit_violation(VT_MULTI_FACE, multi_face_timer)
                    multi_face_flagged = True
            else:
                no_face_timer = multi_face_timer = 0.0
                no_face_flagged = multi_face_flagged = False

            if hand_near_face and face_count >= 1:
                hand_face_timer += dt
                if hand_face_timer >= HAND_FACE_THRESHOLD_SEC and not hand_face_flagged:
                    _emit_violation(VT_HAND_OVER_FACE, hand_face_timer)
                    hand_face_flagged = True
            else:
                hand_face_timer = 0.0; hand_face_flagged = False

            if gesture_label in GESTURE_FLAG_GESTURES and hand_near_face:
                _emit_violation(VT_SUSPICIOUS_GESTURE)

            if phone_visible:
                phone_timer += dt
                if phone_timer >= PHONE_THRESHOLD_SEC and not phone_flagged:
                    _emit_violation(VT_PHONE_DETECTED, phone_timer)
                    phone_flagged = True
            else:
                phone_timer = 0.0; phone_flagged = False

            if object_visible:
                object_timer += dt
                cooldown_ok = (now - object_cooldown_ts.get(object_label, 0)) > OBJECT_COOLDOWN_SEC
                if object_timer >= OBJECT_THRESHOLD_SEC and not object_flagged and cooldown_ok:
                    _emit_violation(VT_CHEATING_OBJECT, object_timer)
                    object_flagged = True
                    object_cooldown_ts[object_label] = now
            else:
                object_timer = 0.0; object_flagged = False

            # ── Overlay ───────────────────────────────────────────────
            annotated = _draw_overlay(
                frame,
                face_count=face_count,
                gesture_label=gesture_label,
                no_face_timer=no_face_timer,
                multi_face_timer=multi_face_timer,
                hand_near_face=hand_near_face,
                violation_count=self.logger.violation_count(),
                detected_objects=last_detections,
                phone_visible=phone_visible,
                yolo_status=yolo_status,
            )

            # ── Encode annotated frame as JPEG base64 ─────────────────
            _, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 70])
            frame_b64 = base64.b64encode(buf.tobytes()).decode()

            state = {
                "type":             "state",
                "face_count":       face_count,
                "gesture":          gesture_label,
                "violations":       self.logger.violation_count(),
                "no_face_timer":    round(no_face_timer, 2),
                "hand_near_face":   hand_near_face,
                "phone_visible":    phone_visible,
                "objects_detected": [d.label for d in last_detections],
                "yolo_status":      yolo_status,
                "frame":            frame_b64,
            }
            self._send(state)

        face_detector.close()
        hand_tracker.close()

    def _send(self, msg: dict) -> None:
        asyncio.run_coroutine_threadsafe(
            self._queue.put(msg), self._loop
        )


# ── Helpers ────────────────────────────────────────────────────────────────

class _LandmarkWrapper:
    def __init__(self, landmark_list):
        self.landmark = landmark_list


_HAND_CONNECTIONS = [
    (0,1),(1,2),(2,3),(3,4),
    (0,5),(5,6),(6,7),(7,8),
    (5,9),(9,10),(10,11),(11,12),
    (9,13),(13,14),(14,15),(15,16),
    (13,17),(17,18),(18,19),(19,20),
    (0,17),
]


def _draw_hand_skeleton(frame, landmarks, w, h):
    pts = [(int(lm.x * w), int(lm.y * h)) for lm in landmarks]
    for a, b in _HAND_CONNECTIONS:
        if a < len(pts) and b < len(pts):
            cv2.line(frame, pts[a], pts[b], (0, 200, 255), 2)
    for x, y in pts:
        cv2.circle(frame, (x, y), 4, (255, 255, 0), -1)
        cv2.circle(frame, (x, y), 4, (0, 0, 0), 1)


def _draw_overlay(frame, *, face_count, gesture_label, no_face_timer,
                  multi_face_timer, hand_near_face, violation_count,
                  detected_objects, phone_visible, yolo_status="YOLO: loading..."):
    h, w = frame.shape[:2]
    overlay = frame.copy()

    if face_count == 0:
        cv2.rectangle(overlay, (0, 0), (w, 60), (0, 0, 200), -1)
        frame = cv2.addWeighted(overlay, 0.55, frame, 0.45, 0)
        _put_text(frame, "  ERROR: No face detected", (10, 42), COLOR_WHITE, scale=1.1, thickness=2)
        if no_face_timer >= 1.0:
            _put_text(frame, f"  ({no_face_timer:.1f}s)", (10, 65), (200, 200, 255), scale=0.7)
    elif face_count > 1:
        _status_bar(frame, f"WARNING: Multiple faces detected ({face_count})", COLOR_ORANGE, y=10)
    else:
        _status_bar(frame, "One face detected", COLOR_GREEN, y=10)

    if phone_visible:
        ov2 = frame.copy()
        cv2.rectangle(ov2, (0, 55), (w, 105), (0, 0, 190), -1)
        frame = cv2.addWeighted(ov2, 0.75, frame, 0.25, 0)
        _put_text(frame, "  *** PHONE / DEVICE DETECTED! ***", (10, 90), (60, 60, 255), scale=0.95, thickness=2)
    elif detected_objects:
        labels = ", ".join(set(d.label for d in detected_objects))
        _put_text(frame, f"  CHEATING OBJECT: {labels.upper()}", (10, 90), COLOR_ORANGE, scale=0.85, thickness=2)

    gesture_display = f"Hand gestures: {gesture_label}"
    if hand_near_face:
        gesture_display += "  [near face]"
    _put_text(frame, gesture_display, (10, h - 45), COLOR_WHITE, scale=0.75)

    vc_color = (0, 60, 220) if violation_count > 0 else (0, 180, 80)
    _put_text(frame, f"Violations: {violation_count}", (10, h - 18), vc_color, scale=0.7, thickness=2)

    _put_text(frame, "PROCTORING LIVE", (w - 210, 28), (80, 220, 80), scale=0.65, thickness=1)
    yolo_color = (80, 220, 80) if "active" in yolo_status else (0, 165, 255)
    _put_text(frame, yolo_status, (w - 210, 50), yolo_color, scale=0.5, thickness=1)

    return frame


def _status_bar(frame, text, color, y=10):
    h, w = frame.shape[:2]
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, y), (w, y + 45), color, -1)
    cv2.addWeighted(overlay, 0.4, frame, 0.6, 0, frame)
    _put_text(frame, f"  {text}", (10, y + 30), COLOR_WHITE, scale=0.85, thickness=2)


def _put_text(frame, text, pos, color, scale=0.8, thickness=2, font=cv2.FONT_HERSHEY_SIMPLEX):
    cv2.putText(frame, text, (pos[0]+1, pos[1]+1), font, scale, COLOR_BLACK, thickness+1, cv2.LINE_AA)
    cv2.putText(frame, text, pos, font, scale, color, thickness, cv2.LINE_AA)
