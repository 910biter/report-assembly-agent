#!/usr/bin/env bash
set -euo pipefail

# Run on an online Linux x86_64 build host with Docker and Compose installed.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="${1:?usage: build-bundle.sh OUTPUT_DIR [ASR_MODEL] [EMBEDDING_REVISION]}"
ASR_MODEL="${2:-medium}"
EMBEDDING_REVISION="${3:-main}"
[[ "$(uname -m)" == x86_64 ]] || { echo "x86_64 required" >&2; exit 1; }
case "$ASR_MODEL" in tiny|base|small|medium|large) ;; *) echo "Unsupported ASR model: $ASR_MODEL" >&2; exit 1;; esac
[[ -z "$(find "$OUT" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]] || {
  echo "Output directory must be empty" >&2; exit 1;
}
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"

docker build --platform linux/amd64 -f "$ROOT/deploy/docker/Dockerfile" \
  --build-arg "PYPI_INDEX_URL=${IRA_PYPI_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}" \
  --build-arg "ASR_MODEL=$ASR_MODEL" \
  --build-arg "EMBEDDING_REVISION=$EMBEDDING_REVISION" \
  -t report-assembly-agent:offline "$ROOT"
docker pull --platform linux/amd64 postgres:16
docker pull --platform linux/amd64 qdrant/qdrant:v1.13.6

docker run --rm --network none --platform linux/amd64 \
  --entrypoint python report-assembly-agent:offline \
  -c 'from pathlib import Path; assert (Path("/opt/models/embedding") / "config.json").is_file(); assert any(Path("/opt/models/embedding").glob("*.safetensors")); assert any(Path("/opt/models/docling").rglob("*.*")); assert any(Path("/home/ira/.cache/whisper").glob("*.pt")); import docling, whisper, torch, onnxruntime'

cp "$ROOT/deploy/docker/compose.yaml" "$OUT/compose.yaml"
sed "s/^IRA_ASR_MODEL=.*/IRA_ASR_MODEL=$ASR_MODEL/" "$ROOT/deploy/docker/app.env.example" > "$OUT/app.env"
cp "$ROOT/deploy/docker/stack.env.example" "$OUT/.env"
cp "$ROOT/deploy/docker/install-bundle.sh" "$OUT/install-bundle.sh"
cp "$ROOT/deploy/docker/install-bundle.ps1" "$OUT/install-bundle.ps1"
cp "$ROOT/deploy/docker/README.md" "$OUT/README.md"
printf 'ASR model: %s\nEmbedding revision: %s\n' "$ASR_MODEL" "$EMBEDDING_REVISION" > "$OUT/bundle.info"
docker image inspect --format '{{.Id}}' \
  report-assembly-agent:offline postgres:16 qdrant/qdrant:v1.13.6 > "$OUT/image-ids.txt"
docker save report-assembly-agent:offline postgres:16 qdrant/qdrant:v1.13.6 \
  | gzip -1 > "$OUT/images.tar.gz"

(cd "$OUT" && sha256sum images.tar.gz compose.yaml install-bundle.sh install-bundle.ps1 README.md bundle.info image-ids.txt) > "$OUT/SHA256SUMS"
echo "Offline bundle ready: $OUT"
