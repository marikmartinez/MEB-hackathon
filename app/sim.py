"""Dev only: random-walk room occupancy so the app can be demoed without cameras.

    python sim.py --url http://localhost:8080 --interval 3

Set OCCUPANCY_TOKEN if the app requires one.
"""
import argparse
import json
import os
import random
import time
import urllib.error
import urllib.request
from pathlib import Path

VENUE = Path(__file__).resolve().parent / "data" / "venue.json"


def post(url, token, payload):
    request = urllib.request.Request(
        f"{url}/api/occupancy",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8080")
    parser.add_argument("--interval", type=float, default=3.0)
    parser.add_argument("--rounds", type=int, default=0, help="0 runs until stopped")
    args = parser.parse_args()

    areas = json.loads(VENUE.read_text())["areas"]
    rooms = [area for area in areas if area["type"] == "room"]
    token = os.environ.get("OCCUPANCY_TOKEN")
    ratios = {room["id"]: random.uniform(0.1, 0.8) for room in rooms}

    completed = 0
    while True:
        for room in rooms:
            ratio = min(max(ratios[room["id"]] + random.uniform(-0.08, 0.08), 0.0), 1.05)
            ratios[room["id"]] = ratio
            payload = {
                "area": room["id"],
                "count": int(ratio * room["capacity"]),
                "capacity": room["capacity"],
            }
            try:
                reading = post(args.url.rstrip("/"), token, payload)
                print(f"{room['id']:10} {reading['count']:4} {reading['level']}")
            except (urllib.error.URLError, TimeoutError) as err:
                print(f"{room['id']:10} failed: {err}")

        completed += 1
        if args.rounds and completed >= args.rounds:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
