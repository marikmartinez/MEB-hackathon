"""Check the counter against known people counts.

    python -m counter.eval                                  # ground_truth.json
    python -m counter.eval --mall ~/data/mall_dataset       # Mall dataset counts

ground_truth.json maps image paths (relative to counter/) to the number of
people in that image. --mall reads the Mall dataset's own counts
(mall_gt.mat, needs scipy) from a local copy: the dataset is not in this repo.
Prints per-image error and the mean absolute error (MAE); exits non-zero if
MAE is above --max-mae.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image

from counter.model import PersonCounter

HERE = Path(__file__).parent


def mall_truth(root: Path, every: int) -> dict[Path, int]:
    from scipy.io import loadmat

    counts = loadmat(root / "mall_gt.mat")["count"].ravel()
    return {root / "frames" / f"seq_{i + 1:06d}.jpg": int(counts[i]) for i in range(0, len(counts), every)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--truth", default=HERE / "ground_truth.json", type=Path)
    parser.add_argument("--mall", type=Path, help="path to an unzipped mall_dataset folder")
    parser.add_argument("--every", default=50, type=int, help="with --mall: use every Nth frame")
    parser.add_argument("--conf", default=0.15, type=float)
    parser.add_argument("--max-mae", default=None, type=float, help="fail if MAE is above this")
    parser.add_argument("--quiet", action="store_true", help="only print the summary")
    args = parser.parse_args()

    if args.mall:
        truth = mall_truth(args.mall.expanduser(), args.every)
    else:
        truth = {HERE / rel: n for rel, n in json.loads(args.truth.read_text()).items()}

    counter = PersonCounter(conf=args.conf)
    errors, latencies = [], []
    for path, expected in truth.items():
        result = counter.count(Image.open(path))
        err = result.count - expected
        errors.append(err)
        latencies.append(result.latency_ms)
        if not args.quiet:
            print(f"{path.name:20} expected {expected:3}  got {result.count:3}  error {err:+3}")

    mae = sum(abs(e) for e in errors) / len(errors)
    bias = sum(errors) / len(errors)
    print(
        f"\n{len(errors)} images  MAE {mae:.2f}  bias {bias:+.2f}  "
        f"mean latency {sum(latencies) / len(latencies):.0f} ms  (conf {args.conf})"
    )
    return 0 if args.max_mae is None or mae <= args.max_mae else 1


if __name__ == "__main__":
    sys.exit(main())
