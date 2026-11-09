"""Turn a video into still frames that stand in for a camera's snapshots.

    pip install -r counter/requirements-dev.txt
    python -m counter.extract_frames VIDEO AREA [--every 10] [--max 60] [--width 640]

Writes counter/frames/<AREA>/0001.jpg, 0002.jpg, ... taking one frame every
--every seconds of footage. The replay loop serves them in order, as if a
camera were screenshotted every tick. Frames are committed and baked into a
public image: only use footage that's fine to publish.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2

FRAMES = Path(__file__).parent / "frames"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("video", type=Path)
    p.add_argument("area", help="area id from venue.json, e.g. hall-a")
    p.add_argument("--every", type=float, default=10.0, help="seconds of footage between frames")
    p.add_argument("--max", type=int, default=60, help="maximum frames to keep")
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--quality", type=int, default=75, help="JPEG quality")
    args = p.parse_args()

    cap = cv2.VideoCapture(str(args.video))
    if not cap.isOpened():
        print(f"can't open {args.video}", file=sys.stderr)
        return 1
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    step = max(1, round(fps * args.every))

    out_dir = FRAMES / args.area
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.jpg"):
        old.unlink()

    written = 0
    for frame_no in range(0, total, step):
        if written >= args.max:
            break
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
        ok, frame = cap.read()
        if not ok:
            break
        h, w = frame.shape[:2]
        if w > args.width:
            frame = cv2.resize(frame, (args.width, round(h * args.width / w)), interpolation=cv2.INTER_AREA)
        written += 1
        cv2.imwrite(str(out_dir / f"{written:04d}.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, args.quality])

    cap.release()
    print(f"{args.area}: wrote {written} frames to {out_dir} (one every {args.every}s of {total / fps:.0f}s footage)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
