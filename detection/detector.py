"""Detector backends.  A missing model never crashes the app."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import List, Optional

import numpy as np

from .postprocess import (Detection, decode_output, letterbox, rescale_boxes, to_detections, to_tensor)

log = logging.getLogger("smartroad.detector")

ST_OK, ST_MISSING, ST_ERROR, ST_DEMO = "OK", "MODEL_MISSING", "ERROR", "DEMO"


class BaseDetector:
    status = ST_ERROR
    message = ""
    backend = "none"
    infer_ms = 0.0

    @property
    def ready(self) -> bool:
        return self.status in (ST_OK, ST_DEMO)

    def detect(self, frame: np.ndarray, meta: Optional[dict] = None) -> List[Detection]:
        return []


class NullDetector(BaseDetector):
    def __init__(self, status: str, message: str):
        self.status, self.message = status, message


class YoloDetector(BaseDetector):
    """ONNX Runtime or TFLite YOLO detector configured from ``model:`` in config."""

    def __init__(self, model_cfg, model_path: Path):
        self.cfg = model_cfg
        self.path = model_path
        self.names = list(model_cfg.classes)
        self.size = int(model_cfg.input_size)
        self._sess = self._interp = None
        suffix = model_path.suffix.lower()
        backend = model_cfg.backend
        if backend == "auto":
            backend = "tflite" if suffix == ".tflite" else "onnx"
        self.backend = backend
        try:
            if backend == "onnx":
                self._load_onnx()
            else:
                self._load_tflite()
            self.status = ST_OK
            log.info("AI model loaded: %s (%s, input %d)", model_path.name, backend, self.size)
        except Exception as exc:  # noqa: BLE001
            self.status, self.message = ST_ERROR, f"{type(exc).__name__}: {exc}"
            log.error("AI model failed to load: %s", exc)

    def _load_onnx(self) -> None:
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.intra_op_num_threads = int(self.cfg.threads)
        self._sess = ort.InferenceSession(str(self.path), so, providers=["CPUExecutionProvider"])
        inp = self._sess.get_inputs()[0]
        self._in_name = inp.name
        if isinstance(inp.shape[2], int):
            self.size = int(inp.shape[2])

    def _load_tflite(self) -> None:
        try:
            from tflite_runtime.interpreter import Interpreter
        except ImportError:
            try:
                from ai_edge_litert.interpreter import Interpreter  # type: ignore
            except ImportError:
                from tensorflow.lite.python.interpreter import Interpreter  # type: ignore
        self._interp = Interpreter(model_path=str(self.path), num_threads=int(self.cfg.threads))
        self._interp.allocate_tensors()
        shp = self._interp.get_input_details()[0]["shape"]  # NHWC
        self.size = int(shp[1])

    def _run(self, tensor_nchw: np.ndarray) -> np.ndarray:
        if self._sess is not None:
            return self._sess.run(None, {self._in_name: tensor_nchw})[0]
        inp = self._interp.get_input_details()[0]
        x = tensor_nchw.transpose(0, 2, 3, 1)
        if inp["dtype"] != np.float32:
            scale, zero = inp["quantization"]
            x = (x / (scale or 1.0) + zero).astype(inp["dtype"])
        self._interp.set_tensor(inp["index"], x)
        self._interp.invoke()
        out = self._interp.get_tensor(self._interp.get_output_details()[0]["index"])
        return out.astype(np.float32)

    def detect(self, frame: np.ndarray, meta: Optional[dict] = None) -> List[Detection]:
        if self.status != ST_OK:
            return []
        h, w = frame.shape[:2]
        t0 = time.perf_counter()
        img, ratio, pad = letterbox(frame, self.size)
        try:
            raw = self._run(to_tensor(img))
            boxes, scores, cls = decode_output(raw, len(self.names), float(self.cfg.confidence),
                                               float(self.cfg.iou), self.cfg.output_format, self.size)
        except Exception as exc:  # noqa: BLE001
            self.status, self.message = ST_ERROR, f"{type(exc).__name__}: {exc}"
            log.error("Inference failed: %s", exc)
            return []
        self.infer_ms = (time.perf_counter() - t0) * 1000
        return to_detections(rescale_boxes(boxes, ratio, pad, (w, h)), scores, cls, self.names)


class DemoDetector(BaseDetector):
    """Demo mode only: returns the synthetic scene's ground-truth boxes.

    The synthetic camera attaches ``meta['truth']`` to each frame; this is NOT
    a real AI model and is never used outside ``--demo``.
    """

    status = ST_DEMO
    backend = "demo"
    message = "SIMULATED DETECTOR (demo mode)"

    def __init__(self, names: List[str]):
        self.names = names

    def detect(self, frame, meta=None):
        out = []
        for t in (meta or {}).get("truth", []):
            out.append(Detection(0, self.names[0], float(t["confidence"]), tuple(t["bbox"])))
        return out


def make_detector(cfg, demo: bool, model_path: Optional[Path] = None) -> BaseDetector:
    """Real model when installed; the simulated detector ONLY for the synthetic demo camera."""
    if demo and str(cfg.camera.backend) in ("auto", "demo"):
        log.info("Demo mode: using the simulated detector for the synthetic camera")
        return DemoDetector(list(cfg.model.classes))
    path = model_path or cfg.resolve(cfg.model.path)
    if path.exists():
        return YoloDetector(cfg.model, path)
    log.warning("AI model not installed at %s", path)
    return NullDetector(ST_MISSING, f"AI MODEL NOT INSTALLED ({path.name})")
