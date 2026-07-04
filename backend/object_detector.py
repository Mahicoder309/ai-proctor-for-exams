"""
object_detector.py
------------------
Cheating-object detector using YOLOv8n in pure OpenCV DNN.
DOES NOT require PyTorch or TensorFlow.

Detects COCO classes that are common cheating tools:
  - cell phone  (COCO class 67)
  - book        (COCO class 73)
  - laptop      (COCO class 63)
  - remote      (COCO class 65)  -> earpiece proxy
  - keyboard    (COCO class 66)
  - mouse       (COCO class 64)

YOLOv8n ONNX is ~12 MB and runs extremely fast on CPU using cv2.dnn.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

# ── COCO class IDs we flag as cheating tools ───────────────────────────────
CHEATING_CLASSES: dict[int, str] = {
    47:  "cup",
    62:  "screen",
    63:  "laptop",
    64:  "mouse",
    65:  "remote",
    66:  "keyboard",
    67:  "cell phone",
    73:  "book",
    74:  "clock",
    76:  "scissors",
}

# BGR colors per label
LABEL_COLORS: dict[str, tuple] = {
    "cell phone": (0,  30, 255),
    "book":       (0, 165, 255),
    "laptop":     (0,  60, 220),
    "remote":     (0, 200, 255),
    "scissors":   (255, 0, 128),
    "keyboard":   (255, 128, 0),
    "mouse":      (128, 255, 0),
    "cup":        (255, 0, 255),
    "clock":      (0, 255, 255),
    "screen":     (0, 255, 128),
}

CONF_THRESHOLD = 0.25
NMS_THRESHOLD  = 0.40


@dataclass
class DetectedObject:
    label:      str
    confidence: float
    x1: int; y1: int; x2: int; y2: int

    @property
    def is_phone(self) -> bool:
        return self.label == "cell phone"

    @property
    def center(self) -> tuple[int, int]:
        return ((self.x1 + self.x2) // 2, (self.y1 + self.y2) // 2)


class CheatingObjectDetector:
    """
    Wraps YOLOv8n ONNX via cv2.dnn for real-time cheating-object detection.
    Does not require torch/tensorflow. Lazy-loads the model.
    """

    def __init__(self, model_name: str = "yolov8n.onnx") -> None:
        self._model_dir  = Path(__file__).parent
        self._model_path = str(self._model_dir / model_name)
        self._net: Optional[cv2.dnn.Net] = None
        self._load_error: Optional[str] = None

    def _ensure_model(self) -> bool:
        if self._net is not None:
            return True
        if self._load_error:
            return False
        try:
            if not os.path.exists(self._model_path):
                raise FileNotFoundError(f"ONNX model file not found at {self._model_path}")
            net = cv2.dnn.readNetFromONNX(self._model_path)
            net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
            self._net = net
            return True
        except Exception as e:
            self._load_error = str(e)
            return False

    def detect(self, frame: np.ndarray) -> list[DetectedObject]:
        if not self._ensure_model():
            return []

        h, w = frame.shape[:2]
        blob = cv2.dnn.blobFromImage(
            frame, scalefactor=1.0/255.0, size=(640, 640),
            mean=(0, 0, 0), swapRB=True, crop=False
        )
        try:
            self._net.setInput(blob)
            outputs = self._net.forward()
        except Exception:
            return []

        output = np.squeeze(outputs[0])
        if output.shape[0] == 84:
            output = output.T

        boxes, confidences, class_ids = [], [], []
        x_factor = w / 640.0
        y_factor = h / 640.0

        for row in output:
            classes_scores = row[4:]
            class_id = np.argmax(classes_scores)
            confidence = classes_scores[class_id]
            if class_id in CHEATING_CLASSES:
                thresh = 0.12 if class_id == 67 else (0.15 if class_id in {62, 63} else 0.20)
                if confidence >= thresh:
                    xc, yc, width, height = row[0:4]
                    left   = int((xc - width / 2) * x_factor)
                    top    = int((yc - height / 2) * y_factor)
                    width  = int(width * x_factor)
                    height = int(height * y_factor)
                    boxes.append([left, top, width, height])
                    confidences.append(float(confidence))
                    class_ids.append(int(class_id))

        indices = cv2.dnn.NMSBoxes(boxes, confidences, CONF_THRESHOLD, NMS_THRESHOLD)
        detected: list[DetectedObject] = []
        if len(indices) > 0:
            for idx in indices.flatten():
                box = boxes[idx]
                left, top, width, height = box
                label = CHEATING_CLASSES[class_ids[idx]]
                x1 = max(0, left); y1 = max(0, top)
                x2 = min(w, left + width); y2 = min(h, top + height)
                detected.append(DetectedObject(
                    label=label, confidence=confidences[idx],
                    x1=x1, y1=y1, x2=x2, y2=y2
                ))
        return detected

    @property
    def available(self) -> bool:
        return self._load_error is None

    @property
    def model_status(self) -> str:
        if self._net is not None:
            return "YOLO: active"
        if self._load_error:
            return "YOLO: error"
        return "YOLO: loading..."


def draw_detections(frame: np.ndarray, detections: list[DetectedObject]) -> None:
    for obj in detections:
        color = LABEL_COLORS.get(obj.label, (0, 0, 255))
        cv2.rectangle(frame, (obj.x1, obj.y1), (obj.x2, obj.y2), color, 2)
        label_text = f"{obj.label} {obj.confidence:.0%}"
        (tw, th), _ = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        cv2.rectangle(frame, (obj.x1, obj.y1 - th - 8), (obj.x1 + tw + 6, obj.y1), color, -1)
        cv2.putText(frame, label_text, (obj.x1 + 3, obj.y1 - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
