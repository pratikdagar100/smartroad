import numpy as np
import pytest

from detection.postprocess import Detection, decode_output, letterbox, nms, rescale_boxes
from detection.tracker import Tracker


def det(x1, y1, x2, y2, c=0.9):
    return Detection(0, "pothole", c, (x1, y1, x2, y2))


def test_tracker_debounces_20_frames_into_one_confirmation():
    t = Tracker(min_stable_frames=5)
    confirmations = 0
    for i in range(20):                       # pothole growing / moving down as the car approaches
        confirmations += len(t.update([det(100 + i, 100 + 3 * i, 140 + 2 * i, 130 + 4 * i)]))
    assert confirmations == 1
    assert len(t.tracks) == 1


def test_tracker_needs_stability_and_expires():
    t = Tracker(min_stable_frames=3, max_misses=2)
    assert t.update([det(0, 0, 10, 10)]) == [] and t.update([det(0, 0, 10, 10)]) == []
    assert len(t.update([det(0, 0, 10, 10)])) == 1
    for _ in range(4):
        t.update([])
    assert t.tracks == []


def test_flicker_does_not_confirm():
    t = Tracker(min_stable_frames=5, max_misses=1)
    for i in range(10):
        assert t.update([det(0, 0, 10, 10)] if i % 3 == 0 else []) == []


def test_two_separate_potholes_two_tracks():
    t = Tracker(min_stable_frames=2)
    n = 0
    for _ in range(3):
        n += len(t.update([det(0, 0, 20, 20), det(300, 0, 320, 20)]))
    assert n == 2


def test_nms():
    boxes = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60]], np.float32)
    assert sorted(nms(boxes, np.array([.9, .8, .7]), 0.5)) == [0, 2]


def test_decode_yolov8_and_rescale():
    out = np.zeros((1, 5, 8400), np.float32)                  # 1 class: cx,cy,w,h,score
    out[0, :, 10] = [320, 320, 100, 60, 0.9]
    out[0, :, 11] = [322, 320, 100, 60, 0.6]                  # duplicate -> removed by NMS
    boxes, scores, _ = decode_output(out, 1, 0.45, 0.5)
    assert len(boxes) == 1 and scores[0] > 0.89
    lb, ratio, pad = letterbox(np.zeros((360, 640, 3), np.uint8), 640)
    assert lb.shape == (640, 640, 3)
    b = rescale_boxes(boxes, ratio, pad, (640, 360))
    assert 0 <= b[0, 0] < b[0, 2] <= 640


def test_decode_yolov5_with_objectness_and_nms_format():
    out = np.zeros((1, 100, 6), np.float32)                   # cx,cy,w,h,obj,cls0
    out[0, 3] = [200, 200, 50, 50, 0.9, 0.9]
    boxes, scores, _ = decode_output(out, 1, 0.45, 0.5)
    assert len(boxes) == 1 and abs(scores[0] - 0.81) < 1e-3
    nms_out = np.zeros((1, 300, 6), np.float32)
    nms_out[0, 0] = [10, 10, 50, 50, 0.8, 0]
    b, _s, c = decode_output(nms_out, 1, 0.45, 0.5, fmt="nms")
    assert len(b) == 1 and c[0] == 0


def test_decode_class_mismatch_is_clear_error():
    with pytest.raises(ValueError, match="model.classes"):
        decode_output(np.zeros((1, 9, 100), np.float32), 1, 0.4, 0.5)
