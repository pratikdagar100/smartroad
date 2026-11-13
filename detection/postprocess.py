"""Pre/post-processing for YOLO-family detectors (v5 / v8 / NMS-exported)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Tuple

import cv2
import numpy as np


@dataclass
class Detection:
    cls_id: int
    cls_name: str
    confidence: float
    bbox: Tuple[float, float, float, float]  # x1,y1,x2,y2 in frame pixels


def letterbox(img: np.ndarray, size: int) -> Tuple[np.ndarray, float, Tuple[float, float]]:
    """Resize keeping aspect ratio, pad to square. Returns (img, ratio, (padx, pady))."""
    h, w = img.shape[:2]
    r = size / max(h, w)
    nw, nh = int(round(w * r)), int(round(h * r))
    resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR) if (nw, nh) != (w, h) else img
    px, py = (size - nw) / 2, (size - nh) / 2
    out = cv2.copyMakeBorder(resized, int(round(py - 0.1)), int(round(py + 0.1)),
                             int(round(px - 0.1)), int(round(px + 0.1)),
                             cv2.BORDER_CONSTANT, value=(114, 114, 114))
    if out.shape[0] != size or out.shape[1] != size:
        out = cv2.resize(out, (size, size))
    return out, r, (px, py)


def to_tensor(img_bgr: np.ndarray) -> np.ndarray:
    rgb = img_bgr[:, :, ::-1]
    return np.ascontiguousarray(rgb.transpose(2, 0, 1)[None], dtype=np.float32) / 255.0


def nms(boxes: np.ndarray, scores: np.ndarray, iou_thr: float) -> List[int]:
    """Plain numpy NMS on xyxy boxes."""
    if len(boxes) == 0:
        return []
    x1, y1, x2, y2 = boxes.T
    areas = np.maximum(0, x2 - x1) * np.maximum(0, y2 - y1)
    order = scores.argsort()[::-1]
    keep: List[int] = []
    while order.size:
        i = order[0]
        keep.append(int(i))
        if order.size == 1:
            break
        rest = order[1:]
        xx1, yy1 = np.maximum(x1[i], x1[rest]), np.maximum(y1[i], y1[rest])
        xx2, yy2 = np.minimum(x2[i], x2[rest]), np.minimum(y2[i], y2[rest])
        inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
        iou = inter / (areas[i] + areas[rest] - inter + 1e-9)
        order = rest[iou <= iou_thr]
    return keep


def decode_output(out: np.ndarray, num_classes: int, conf_thr: float, iou_thr: float,
                  fmt: str = "auto", input_size: int = 640
                  ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Decode a raw model output into (boxes_xyxy, scores, class_ids) in *input* pixels."""
    a = np.asarray(out)
    if a.ndim == 3:
        a = a[0]
    if a.ndim != 2:
        raise ValueError(f"unexpected model output shape {out.shape}")

    if fmt == "nms":  # rows: x1,y1,x2,y2,score,cls
        rows = a if a.shape[1] >= 6 else a.T
        rows = rows[rows[:, 4] >= conf_thr]
        boxes, scores, cls = rows[:, :4], rows[:, 4], rows[:, 5].astype(int)
        if len(boxes) and boxes.max() <= 1.5:
            boxes = boxes * input_size
        return boxes.astype(np.float32), scores.astype(np.float32), cls

    # channels-first (c, N) -> (N, c)
    if fmt == "yolov8" or (fmt == "auto" and a.shape[0] < a.shape[1]):
        a = a.T
    c = a.shape[1]
    if fmt == "yolov5":
        has_obj = True
    elif fmt == "yolov8":
        has_obj = False
    else:
        has_obj = (c == 5 + num_classes)
        # YOLOv8-seg heads append 32 mask coefficients after the class scores (ignored here)
        if not has_obj and c not in (4 + num_classes, 4 + num_classes + 32):
            raise ValueError(f"model has {c} output channels, which does not match "
                             f"{num_classes} configured classes (expected {4 + num_classes}, {5 + num_classes} or {36 + num_classes} for -seg); "
                             f"fix model.classes in config.yaml")
    xywh = a[:, :4]
    if has_obj:
        cls_scores = a[:, 5:5 + num_classes] * a[:, 4:5]
    else:
        cls_scores = a[:, 4:4 + num_classes]
    cls = cls_scores.argmax(1)
    scores = cls_scores[np.arange(len(cls)), cls]
    m = scores >= conf_thr
    xywh, scores, cls = xywh[m], scores[m], cls[m]
    if len(xywh) and xywh.max() <= 1.5:  # normalised (tflite exports)
        xywh = xywh * input_size
    boxes = np.empty_like(xywh)
    boxes[:, 0] = xywh[:, 0] - xywh[:, 2] / 2
    boxes[:, 1] = xywh[:, 1] - xywh[:, 3] / 2
    boxes[:, 2] = xywh[:, 0] + xywh[:, 2] / 2
    boxes[:, 3] = xywh[:, 1] + xywh[:, 3] / 2
    # class-aware NMS via coordinate offset
    off = cls[:, None].astype(np.float32) * 4096
    keep = nms(boxes + off, scores, iou_thr)
    return boxes[keep].astype(np.float32), scores[keep].astype(np.float32), cls[keep]


def rescale_boxes(boxes: np.ndarray, ratio: float, pad: Tuple[float, float],
                  frame_wh: Tuple[int, int]) -> np.ndarray:
    if len(boxes) == 0:
        return boxes
    b = boxes.copy()
    b[:, [0, 2]] = (b[:, [0, 2]] - pad[0]) / ratio
    b[:, [1, 3]] = (b[:, [1, 3]] - pad[1]) / ratio
    b[:, [0, 2]] = b[:, [0, 2]].clip(0, frame_wh[0])
    b[:, [1, 3]] = b[:, [1, 3]].clip(0, frame_wh[1])
    return b


def to_detections(boxes: np.ndarray, scores: np.ndarray, cls: np.ndarray,
                  names: Sequence[str]) -> List[Detection]:
    out = []
    for b, s, c in zip(boxes, scores, cls):
        name = names[int(c)] if int(c) < len(names) else f"class{int(c)}"
        out.append(Detection(int(c), name, float(s), tuple(float(v) for v in b)))  # type: ignore[arg-type]
    return out
