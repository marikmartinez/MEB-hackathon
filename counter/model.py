"""Count people in an image with YOLO11-nano (ONNX, CPU).

Only onnxruntime, numpy and pillow at runtime: no torch, so the image stays small.

    python -m counter.model counter/samples/bus.jpg
"""
from __future__ import annotations

import io
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image, ImageDraw

DEFAULT_MODEL = Path(__file__).parent / "models" / "yolo11n.onnx"
PERSON_CLASS = 0  # COCO class 0


@dataclass
class Detection:
    x1: float
    y1: float
    x2: float
    y2: float
    score: float


@dataclass
class CountResult:
    count: int
    boxes: list[Detection] = field(default_factory=list)
    latency_ms: float = 0.0


class PersonCounter:
    def __init__(
        self,
        model_path: str | os.PathLike = DEFAULT_MODEL,
        conf: float = 0.35,
        iou: float = 0.45,
        threads: int = 1,
    ):
        opts = ort.SessionOptions()
        # Match the pod's CPU limit, so inference doesn't fight itself for cores.
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(model_path), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self.input_name = self.session.get_inputs()[0].name
        _, _, self.size, _ = self.session.get_inputs()[0].shape  # [1, 3, 640, 640]
        self.conf = conf
        self.iou = iou

    def _letterbox(self, img: Image.Image) -> tuple[np.ndarray, float, int, int]:
        """Resize keeping aspect ratio and pad to a square, like YOLO training."""
        w, h = img.size
        scale = self.size / max(w, h)
        nw, nh = round(w * scale), round(h * scale)
        canvas = Image.new("RGB", (self.size, self.size), (114, 114, 114))
        pad_x, pad_y = (self.size - nw) // 2, (self.size - nh) // 2
        canvas.paste(img.resize((nw, nh), Image.BILINEAR), (pad_x, pad_y))
        arr = np.asarray(canvas, dtype=np.float32) / 255.0
        return arr.transpose(2, 0, 1)[None], scale, pad_x, pad_y

    def count(self, image: Image.Image | bytes) -> CountResult:
        if isinstance(image, (bytes, bytearray)):
            image = Image.open(io.BytesIO(image))
        image = image.convert("RGB")
        start = time.perf_counter()
        tensor, scale, pad_x, pad_y = self._letterbox(image)
        # Output [1, 4 + 80, N]: cx, cy, w, h, then one score per COCO class.
        out = self.session.run(None, {self.input_name: tensor})[0][0]
        scores = out[4 + PERSON_CLASS]
        keep = scores >= self.conf
        cx, cy, bw, bh = out[:4, keep]
        scores = scores[keep]
        boxes = np.stack([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], axis=1)
        idx = nms(boxes, scores, self.iou)
        # Undo letterboxing so boxes are in original image pixels.
        boxes = (boxes[idx] - [pad_x, pad_y, pad_x, pad_y]) / scale
        dets = [Detection(*map(float, b), float(s)) for b, s in zip(boxes, scores[idx])]
        latency = (time.perf_counter() - start) * 1000
        return CountResult(count=len(dets), boxes=dets, latency_ms=latency)


def nms(boxes: np.ndarray, scores: np.ndarray, iou_thresh: float) -> list[int]:
    """Greedy non-maximum suppression; returns kept indices, best first."""
    if len(boxes) == 0:
        return []
    x1, y1, x2, y2 = boxes.T
    areas = (x2 - x1) * (y2 - y1)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size:
        i = int(order[0])
        keep.append(i)
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-9)
        order = order[1:][iou < iou_thresh]
    return keep


def draw_boxes(image: Image.Image, result: CountResult) -> Image.Image:
    """Copy of the image with person boxes and the count drawn on it."""
    out = image.convert("RGB").copy()
    draw = ImageDraw.Draw(out)
    for d in result.boxes:
        draw.rectangle([d.x1, d.y1, d.x2, d.y2], outline=(255, 64, 64), width=3)
    draw.rectangle([0, 0, 170, 28], fill=(0, 0, 0))
    draw.text((8, 8), f"people: {result.count}", fill=(255, 255, 255))
    return out


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: python -m counter.model IMAGE [IMAGE ...]", file=sys.stderr)
        return 2
    counter = PersonCounter()
    for path in argv:
        r = counter.count(Image.open(path))
        print(f"{path}: {r.count} people ({r.latency_ms:.0f} ms)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
