"""Build per-area replay frames from a local copy of the Mall dataset.

    python -m counter.mall_frames ~/data/mall_dataset

The Mall dataset is one camera, so each venue area gets a different stretch
of its footage (some quieter, some busier) to give the dashboard a mix of
crowd levels. Writes counter/frames/<area>/NNNN.jpg, which is git-ignored:
until the dataset's licence is confirmed, we don't redistribute it.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

FRAMES = Path(__file__).parent / "frames"

# venue.json area id -> first Mall frame (1-based) of its stretch. Busiest
# stretches (36-38 people) go to the 60-capacity areas, the quietest (22-23)
# to big ones, so the dashboard shows a mix of levels.
DEFAULT_STRETCHES = {
    "room-104": 351,  # ~38 people / 60
    "room-103": 901,  # ~37 / 60
    "corridor-north": 401,  # ~36 / 60
    "corridor-east": 251,  # ~22 / 80
    "atrium": 151,  # ~23 / 400
}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("mall", type=Path, help="path to an unzipped mall_dataset folder")
    p.add_argument("--frames", type=int, default=20, help="frames per area")
    p.add_argument("--step", type=int, default=5, help="take every Nth dataset frame")
    args = p.parse_args()

    src = args.mall.expanduser() / "frames"
    if not src.is_dir():
        print(f"no frames/ folder in {args.mall}", file=sys.stderr)
        return 1
    for area, start in DEFAULT_STRETCHES.items():
        out = FRAMES / area
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)
        for n in range(args.frames):
            seq = start + n * args.step
            shutil.copy(src / f"seq_{seq:06d}.jpg", out / f"{n + 1:04d}.jpg")
        print(f"{area}: Mall frames {start}-{start + (args.frames - 1) * args.step} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
