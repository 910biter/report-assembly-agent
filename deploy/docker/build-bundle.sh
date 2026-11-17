#!/usr/bin/env bash
set -euo pipefail

# Run on an online Linux x86_64 build host with Docker and Compose installed.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="${1:?usage: build-bundle.sh OUTPUT_DIR [ASR_MODEL]}"
ASR_MODEL="${2:-medium}"
[[ $# -le 2 ]] || { echo "Model weights are inherited from the base image; choose a matching ASR model" >&2; exit 1; }
BASE_IMAGE="${IRA_BASE_IMAGE:-report-assembly-agent-base:offline}"
SOURCE_BRANCH="$(git -C "$ROOT" branch --show-current)"
SOURCE_COMMIT="$(git -C "$ROOT" rev-parse HEAD)"
[[ "$SOURCE_BRANCH" == 0824 ]] || { echo "Build from the 0824 branch (found: $SOURCE_BRANCH)" >&2; exit 1; }
SOURCE_STATE=clean
if [[ -n "$(git -C "$ROOT" status --porcelain)" ]]; then
  [[ "${IRA_ALLOW_DIRTY_BUILD:-0}" == 1 ]] || {
    echo "Commit source changes before building, or set IRA_ALLOW_DIRTY_BUILD=1 for a test bundle" >&2
    exit 1
  }
  SOURCE_STATE=uncommitted
fi
[[ "$(uname -m)" == x86_64 ]] || { echo "x86_64 build host required" >&2; exit 1; }
case "$ASR_MODEL" in tiny|base|small|medium|large) ;; *) echo "Unsupported ASR model: $ASR_MODEL" >&2; exit 1;; esac
[[ -z "$(find "$OUT" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]] || {
  echo "Output directory must be empty" >&2; exit 1;
}
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"

if ! docker image inspect "$BASE_IMAGE" >/dev/null 2>&1; then
  docker image inspect report-assembly-agent:offline >/dev/null 2>&1 || {
    echo "Missing base and source images; load the previous package image first" >&2
    exit 1
  }
  docker build --platform linux/amd64 -f "$ROOT/deploy/docker/Dockerfile.base" \
    -t "$BASE_IMAGE" "$ROOT"
fi
docker run --rm --network none --platform linux/amd64 --entrypoint bash "$BASE_IMAGE" \
  -lc "test ! -e /opt/app/app && test -f /home/ira/.cache/whisper/$ASR_MODEL.pt && test -f /opt/models/embedding/config.json && python -m pip check"
docker build --platform linux/amd64 -f "$ROOT/deploy/docker/Dockerfile" \
  --build-arg "BASE_IMAGE=$BASE_IMAGE" \
  -t report-assembly-agent:offline "$ROOT"
for image in postgres:16 qdrant/qdrant:v1.13.6; do
  if ! docker image inspect "$image" >/dev/null 2>&1; then
    docker pull --platform linux/amd64 "$image"
  fi
done

for image in report-assembly-agent:offline postgres:16 qdrant/qdrant:v1.13.6; do
  actual="$(docker image inspect --format '{{.Architecture}}' "$image")"
  [[ "$actual" == amd64 ]] || { echo "$image is $actual, expected amd64" >&2; exit 1; }
done

docker run --rm --network none --platform linux/amd64 \
  --entrypoint python report-assembly-agent:offline \
  -c 'from pathlib import Path; assert (Path("/opt/models/embedding") / "config.json").is_file(); assert any(Path("/opt/models/embedding").glob("*.safetensors")); assert any(Path("/opt/models/docling").rglob("*.*")); assert any(Path("/home/ira/.cache/whisper").glob("*.pt")); import docling, whisper, torch, onnxruntime'

cp "$ROOT/deploy/docker/compose.yaml" "$OUT/compose.yaml"
sed "s/^IRA_ASR_MODEL=.*/IRA_ASR_MODEL=$ASR_MODEL/" "$ROOT/deploy/docker/app.env.example" > "$OUT/app.env"
cp "$ROOT/deploy/docker/stack.env.example" "$OUT/.env"
cp "$ROOT/deploy/docker/install-bundle.sh" "$OUT/install-bundle.sh"
cp "$ROOT/deploy/docker/install-bundle.ps1" "$OUT/install-bundle.ps1"
cp "$ROOT/deploy/docker/README.md" "$OUT/README.md"
printf 'Source branch: %s\nSource commit: %s\nSource state: %s\nArchitecture: %s\nASR model: %s\nBase image: %s\nBase image ID: %s\n' \
  "$SOURCE_BRANCH" "$SOURCE_COMMIT" "$SOURCE_STATE" "amd64" "$ASR_MODEL" "$BASE_IMAGE" "$(docker image inspect --format '{{.Id}}' "$BASE_IMAGE")" > "$OUT/bundle.info"
docker image inspect --format '{{.Id}}' \
  report-assembly-agent:offline postgres:16 qdrant/qdrant:v1.13.6 > "$OUT/image-ids.txt"
docker save report-assembly-agent:offline postgres:16 qdrant/qdrant:v1.13.6 \
  | gzip -1 > "$OUT/images.tar.gz"

(cd "$OUT" && sha256sum images.tar.gz compose.yaml install-bundle.sh install-bundle.ps1 README.md bundle.info image-ids.txt) > "$OUT/SHA256SUMS"
echo "Offline bundle ready: $OUT"
