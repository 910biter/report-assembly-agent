#!/usr/bin/env bash
set -euo pipefail

PACKAGE_DIR=/opt/report-assembly-agent
STATE_DIR=/var/lib/report-assembly-agent
export COMPOSE_PROJECT_NAME=report-assembly-agent

check_host() {
  [[ "$(uname -m)" == x86_64 ]] || { echo "x86_64 Linux is required" >&2; exit 1; }
  command -v docker >/dev/null || { echo "Docker Engine is required" >&2; exit 1; }
  docker info >/dev/null 2>&1 || { echo "Docker Engine is not running" >&2; exit 1; }
  docker compose version >/dev/null 2>&1 || { echo "Docker Compose plugin is required" >&2; exit 1; }
  [[ "$(docker info --format '{{.OSType}}')" == linux ]] || { echo "Docker must run Linux containers" >&2; exit 1; }
  case "$(docker info --format '{{.Architecture}}')" in x86_64|amd64) ;; *) echo "Docker daemon must be x86_64" >&2; exit 1;; esac
}

prepare_state() {
  umask 077
  mkdir -p "$STATE_DIR/data/app"
  cp "$PACKAGE_DIR/compose.yaml" "$STATE_DIR/compose.yaml"
  [[ -e "$STATE_DIR/app.env" ]] || cp "$PACKAGE_DIR/app.env" "$STATE_DIR/app.env"
  [[ -e "$STATE_DIR/.env" ]] || cp "$PACKAGE_DIR/stack.env.example" "$STATE_DIR/.env"
  if grep -q '^POSTGRES_PASSWORD=replace-with-a-strong-password$' "$STATE_DIR/.env"; then
    secret="$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')"
    sed -i "s/^POSTGRES_PASSWORD=replace-with-a-strong-password$/POSTGRES_PASSWORD=$secret/" "$STATE_DIR/.env"
  fi
  chmod 600 "$STATE_DIR/.env"
  uid="$(sed -n 's/^APP_UID=//p' "$STATE_DIR/.env" | tail -1)"
  gid="$(sed -n 's/^APP_GID=//p' "$STATE_DIR/.env" | tail -1)"
  chown "${uid:-1000}:${gid:-1000}" "$STATE_DIR/data/app"
}

compose() {
  cd "$STATE_DIR"
  docker compose --env-file .env "$@"
}

image_ids() {
  docker image inspect --format '{{.Id}}' \
    report-assembly-agent:offline postgres:16 qdrant/qdrant:v1.13.6 2>/dev/null
}

load_images() {
  cd "$PACKAGE_DIR"
  sha256sum -c image.sha256 --quiet
  local archive_digest actual_ids recorded_ids
  archive_digest="$(cut -d ' ' -f 1 image.sha256)"
  actual_ids="$(image_ids || true)"
  recorded_ids="$(cat "$STATE_DIR/loaded-image-ids" 2>/dev/null || true)"
  if [[ -f "$STATE_DIR/loaded-image-digest" \
     && "$(cat "$STATE_DIR/loaded-image-digest")" == "$archive_digest" \
     && -n "$actual_ids" && "$actual_ids" == "$recorded_ids" ]]; then
    return
  fi
  docker load -i "$PACKAGE_DIR/images.tar.gz"
  actual_ids="$(image_ids)"
  [[ "$(printf '%s\n' "$actual_ids" | wc -l)" -eq 3 ]] || {
    echo "The package did not import all three images" >&2; exit 1;
  }
  for image in report-assembly-agent:offline postgres:16 qdrant/qdrant:v1.13.6; do
    [[ "$(docker image inspect --format '{{.Architecture}}' "$image")" == amd64 ]] || {
      echo "Imported image has the wrong architecture: $image" >&2; exit 1;
    }
  done
  printf '%s\n' "$actual_ids" > "$STATE_DIR/loaded-image-ids.tmp"
  printf '%s\n' "$archive_digest" > "$STATE_DIR/loaded-image-digest.tmp"
  mv "$STATE_DIR/loaded-image-ids.tmp" "$STATE_DIR/loaded-image-ids"
  mv "$STATE_DIR/loaded-image-digest.tmp" "$STATE_DIR/loaded-image-digest"
}

ensure_admin() {
  local status credential_file
  status="$(compose run --rm -T app python -m app.auth.bootstrap --status)"
  case "$status" in
    admin-ready) return ;;
    needs-bootstrap) ;;
    *) echo "Cannot determine administrator bootstrap state: $status" >&2; exit 1 ;;
  esac
  credential_file="$STATE_DIR/initial-admin-password"
  if [[ ! -e "$credential_file" ]]; then
    umask 077
    od -An -N24 -tx1 /dev/urandom | tr -d ' \n' > "$credential_file"
    printf '\n' >> "$credential_file"
  fi
  cat "$credential_file" | compose run --rm -T app \
    python -m app.auth.bootstrap admin --password-stdin
  echo "Administrator: admin; initial password: $credential_file (root-only)"
}

start() {
  check_host
  prepare_state
  load_images
  compose config --quiet
  ensure_admin
  compose up -d --pull never
  for _ in $(seq 1 60); do
    if compose exec -T app python -c 'from urllib.request import urlopen; assert urlopen("http://127.0.0.1:8000/api/health", timeout=3).status == 200' >/dev/null 2>&1; then
      compose ps
      return
    fi
    sleep 2
  done
  compose ps >&2
  echo "Application did not become healthy; inspect: docker compose logs app" >&2
  exit 1
}

case "${1:-}" in
  check) check_host ;;
  start) start ;;
  stop) [[ -d "$STATE_DIR" ]] && compose stop || true ;;
  status) [[ -d "$STATE_DIR" ]] && compose ps || true ;;
  *) echo "Usage: $0 {check|start|stop|status}" >&2; exit 2 ;;
esac
