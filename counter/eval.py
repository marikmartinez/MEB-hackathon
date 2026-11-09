"""Check the counter against hand counts in ground_truth.json.

    python -m counter.eval

ground_truth.json maps image paths (relative to counter/) to the number of
people a person counted in that image. Prints per-image error and the mean
absolute error (MAE); exits non-zero if MAE is above --max-mae.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image

from counter.model import PersonCounter

HERE = Path(__file__).parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--truth", default=HERE / "ground_truth.json", type=Path)
    parser.add_argument("--conf", default=0.35, type=float)
    parser.add_argument("--max-mae", default=1.0, type=float)
    args = parser.parse_args()

    truth: dict[str, int] = json.loads(args.truth.read_text())
    counter = PersonCounter(conf=args.conf)
    errors, latencies = [], []
    for rel, expected in truth.items():
        result = counter.count(Image.open(HERE / rel))
        err = result.count - expected
        errors.append(abs(err))
        latencies.append(result.latency_ms)
        print(f"{rel:45} expected {expected:3}  got {result.count:3}  error {err:+3}")

    mae = sum(errors) / len(errors)
    print(f"\n{len(errors)} images  MAE {mae:.2f}  mean latency {sum(latencies) / len(latencies):.0f} ms")
    return 0 if mae <= args.max_mae else 1


if __name__ == "__main__":
    sys.exit(main())
