#!/usr/bin/env bash
# Upload the counter's replay frames to the cluster as one Secret per room.
#
#   python -m counter.mall_frames ~/path/to/mall_dataset   # builds counter/frames/<room>/
#   export KUBECONFIG=~/cnh-kubeconfig                      # README, "kubectl"
#   ./scripts/upload-frames.sh
#
# The frames never go into git or the public image: they live only in these
# Secrets, which deploy/counter-deployment.yaml mounts at frames/<room>/.
# Re-running replaces them; the counter picks up new frames within about a
# minute, no restart needed.
set -euo pipefail

NAMESPACE="${NAMESPACE:-team-meb}"
FRAMES_DIR="$(cd "$(dirname "$0")/.." && pwd)/counter/frames"
ROOMS=(hall-a hall-b room-101 room-102 room-103 room-104)
# Kubernetes caps a Secret at 1 MiB.
MAX_BYTES=$((1024 * 1024 - 16 * 1024))

kubectl get namespace "$NAMESPACE" >/dev/null

for room in "${ROOMS[@]}"; do
  dir="$FRAMES_DIR/$room"
  if ! compgen -G "$dir/*.jpg" >/dev/null; then
    echo "skip $room: no frames in $dir"
    continue
  fi
  bytes=$(cat "$dir"/*.jpg | wc -c | tr -d ' ')
  if (( bytes > MAX_BYTES )); then
    echo "skip $room: $bytes bytes is over the Secret limit; use fewer frames (mall_frames --frames 15)" >&2
    continue
  fi
  # Server-side apply: client-side apply would store a second copy of the
  # data in an annotation and push the Secret over the size limit.
  kubectl -n "$NAMESPACE" create secret generic "counter-frames-$room" \
    --from-file="$dir" --dry-run=client -o yaml \
    | kubectl label --local -f - app=counter purpose=replay-frames -o yaml \
    | kubectl apply --server-side --force-conflicts -f - >/dev/null
  echo "uploaded $room: $(ls "$dir"/*.jpg | wc -l | tr -d ' ') frames, $((bytes / 1024)) KiB"
done
