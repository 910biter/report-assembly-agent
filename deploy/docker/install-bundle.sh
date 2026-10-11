#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"
[[ "$(uname -m)" == x86_64 ]] || { echo "x86_64 required" >&2; exit 1; }
command -v docker >/dev/null || { echo "Install Docker Engine first" >&2; exit 1; }
docker compose version >/dev/null || { echo "Install Docker Compose plugin first" >&2; exit 1; }
[[ -f .env && -f app.env ]] || { echo "Missing .env or app.env" >&2; exit 1; }
if grep -q '^POSTGRES_PASSWORD=replace-with-a-strong-password$' .env; then
  echo "Set a unique POSTGRES_PASSWORD in .env before installation" >&2
  exit 1
fi
sha256sum -c SHA256SUMS --quiet
mkdir -p data/app
docker load -i images.tar.gz
docker compose --env-file .env config --quiet
docker compose --env-file .env up -d --pull never
docker compose --env-file .env ps
echo "Application: http://127.0.0.1:$(sed -n 's/^APP_PORT=//p' .env | tail -1)"
