#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"
case "$(uname -m)" in x86_64) HOST_ARCH=amd64;; *) echo "This bundle requires x86_64 Linux" >&2; exit 1;; esac
if grep -q '^Architecture:' bundle.info; then
  BUNDLE_ARCH="$(sed -n 's/^Architecture: //p' bundle.info)"
  [[ "$BUNDLE_ARCH" == "$HOST_ARCH" ]] || { echo "Bundle is $BUNDLE_ARCH; host is $HOST_ARCH" >&2; exit 1; }
else
  [[ "$HOST_ARCH" == amd64 ]] || { echo "Legacy bundle has amd64 images only" >&2; exit 1; }
fi
for tool in docker sha256sum; do
  command -v "$tool" >/dev/null || { echo "Missing required command: $tool" >&2; exit 1; }
done
docker info >/dev/null 2>&1 || { echo "Docker Engine is not running or this user cannot access it" >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "Install the Docker Compose plugin first" >&2; exit 1; }
[[ "$(docker info --format '{{.OSType}}')" == linux ]] || { echo "Docker must run Linux containers" >&2; exit 1; }
case "$(docker info --format '{{.Architecture}}')" in x86_64|amd64) ;; *) echo "Docker daemon must be x86_64" >&2; exit 1;; esac
[[ -f .env && -f app.env ]] || { echo "Missing .env or app.env" >&2; exit 1; }
sha256sum -c SHA256SUMS --quiet
if grep -q '^POSTGRES_PASSWORD=replace-with-a-strong-password$' .env; then
  secret="$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')"
  sed -i "s/^POSTGRES_PASSWORD=replace-with-a-strong-password$/POSTGRES_PASSWORD=$secret/" .env
  chmod 600 .env
  echo "Generated a database password in .env; keep this file private and backed up"
fi
mkdir -p data/app
app_uid="$(sed -n 's/^APP_UID=//p' .env | tail -1)"
app_gid="$(sed -n 's/^APP_GID=//p' .env | tail -1)"
if [[ "$(id -u)" == 0 ]]; then
  chown "${app_uid:-1000}:${app_gid:-1000}" data/app
elif [[ ! -w data/app ]]; then
  echo "data/app is not writable; run as root or fix APP_UID/APP_GID permissions" >&2
  exit 1
fi
docker load -i images.tar.gz
docker compose --env-file .env config --quiet
status="$(docker compose --env-file .env run --rm -T app python -m app.auth.bootstrap --status)"
case "$status" in
  admin-ready) ;;
  needs-bootstrap)
    credential_file="$DIR/initial-admin-password"
    if [[ ! -e "$credential_file" ]]; then
      umask 077
      od -An -N24 -tx1 /dev/urandom | tr -d ' \n' > "$credential_file"
      printf '\n' >> "$credential_file"
    fi
    cat "$credential_file" | docker compose --env-file .env run --rm -T app \
      python -m app.auth.bootstrap admin --password-stdin
    echo "Administrator: admin; initial password: $credential_file"
    ;;
  *) echo "Cannot determine administrator bootstrap state: $status" >&2; exit 1 ;;
esac
docker compose --env-file .env up -d --pull never
docker compose --env-file .env ps
port="${APP_PORT:-$(sed -n 's/^APP_PORT=//p' .env | tail -1)}"
if command -v curl >/dev/null; then
  ready=0
  for _ in $(seq 1 60); do
    if curl -fsS --max-time 3 "http://127.0.0.1:${port:-8000}/api/health" >/dev/null 2>&1; then ready=1; break; fi
    sleep 2
  done
  [[ "$ready" == 1 ]] || { echo "Application did not become healthy; inspect: docker compose logs app" >&2; exit 1; }
else
  echo "curl not installed; skipped HTTP health probe" >&2
fi
echo "Application: http://127.0.0.1:${port:-8000}"
