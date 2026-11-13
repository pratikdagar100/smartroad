# SmartRoad models

No model is bundled (they are large and must be trained on your roads). Without one the UI shows **AI MODEL NOT INSTALLED**;
`--demo` uses a simulated detector instead.

## What the app expects
| Item | Value |
|---|---|
| File | `models/pothole.onnx` (change `model.path` in `config.yaml`) |
| Format | ONNX (onnxruntime) or `.tflite` (needs `tflite-runtime` / `ai-edge-litert`) |
| Family | YOLOv5 / YOLOv8 / YOLO11 style detectors (`model.output_format: auto`) |
| Input | `1×3×S×S` float32 RGB 0–1 (letterboxed); S read from the model, default 640 |
| Output | YOLOv8: `1×(4+nc)×N`; YOLOv5: `1×N×(5+nc)`; NMS-exported: `1×N×6` → set `output_format: nms` |
| Classes | `model.classes` in **training order**, e.g. `[pothole]` or `[pothole, crack, speed_bump, obstruction]` |
| Thresholds | `model.confidence` 0.45, `model.iou` 0.50; event gate `events.min_confidence` 0.55 |

New classes need no code change: add the name to `model.classes`; hazards are stored per `hazard_type`.

## Getting a model
Train with Ultralytics on a pothole dataset (e.g. Roboflow Universe, Kaggle "BharatPotHole", RDD2022), ideally plus
frames from your own dashcam:
```bash
pip install ultralytics
yolo detect train data=pothole.yaml model=yolov8n.pt imgsz=640 epochs=100
yolo export model=runs/detect/train/weights/best.pt format=onnx imgsz=640 opset=12 simplify=True
cp best.onnx models/pothole.onnx
```
TFLite (INT8, faster on Pi 4): `yolo export model=best.pt format=tflite int8=True imgsz=320 data=pothole.yaml`
→ set `model.path: models/pothole_int8.tflite`, `model.input_size: 320`.

## Raspberry Pi optimisation
* Prefer nano/small models (`yolov8n`). Export at `imgsz=320` or `416` for Pi 4; 640 is fine on Pi 5.
* `model.frame_skip: 2` halves inference load; `model.threads: 4`.
* 64-bit Raspberry Pi OS is required for onnxruntime wheels.
* Future: Hailo-8L (Pi AI Kit) or Raspberry Pi AI Camera via a new `BaseDetector` subclass in `detection/detector.py`.

## Verify
`python -m app.hardware_test` prints `[OK] AI model pothole.onnx (onnx, input 640)`.
