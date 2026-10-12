#!/usr/bin/env bash
set -euo pipefail

# Produce one DEB and one RPM from a validated x86_64 image bundle.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BUNDLE="${1:?usage: build-linux-packages.sh BUNDLE_DIR OUTPUT_DIR [VERSION]}"
OUT="${2:?usage: build-linux-packages.sh BUNDLE_DIR OUTPUT_DIR [VERSION]}"
VERSION="${3:-0.1.0}"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Version must be X.Y.Z" >&2; exit 1; }
command -v dpkg-deb >/dev/null || { echo "dpkg-deb required" >&2; exit 1; }
command -v rpmbuild >/dev/null || { echo "rpmbuild required" >&2; exit 1; }
[[ "$(uname -m)" == x86_64 ]] || { echo "x86_64 build host required" >&2; exit 1; }
BUNDLE="$(cd "$BUNDLE" && pwd)"
[[ -f "$BUNDLE/images.tar.gz" ]] || { echo "Missing images.tar.gz" >&2; exit 1; }
(cd "$BUNDLE" && sha256sum -c SHA256SUMS --quiet)
[[ "$(sed -n 's/^Architecture: //p' "$BUNDLE/bundle.info")" == amd64 ]] || {
  echo "Only x86_64 bundle supported" >&2; exit 1;
}
[[ "$(sed -n 's/^Source branch: //p' "$BUNDLE/bundle.info")" == 0824 \
   && "$(sed -n 's/^Source state: //p' "$BUNDLE/bundle.info")" == clean ]] || {
  echo "Package requires a clean bundle built from 0824" >&2; exit 1;
}
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
DEB="$OUT/report-assembly-agent_${VERSION}_amd64.deb"
RPM="$OUT/report-assembly-agent-${VERSION}-1.x86_64.rpm"
[[ ! -e "$DEB" && ! -e "$RPM" ]] || { echo "Output package already exists" >&2; exit 1; }
WORK="$(mktemp -d "$OUT/.package-work.XXXXXX")"
trap 'rm -rf -- "$WORK"' EXIT
export TMPDIR="$WORK"

mkdir -p "$WORK/stage/opt/report-assembly-agent" "$WORK/stage/usr/lib/systemd/system"
cp "$BUNDLE/compose.yaml" "$BUNDLE/app.env" "$WORK/stage/opt/report-assembly-agent/"
cp "$BUNDLE/bundle.info" "$WORK/stage/opt/report-assembly-agent/"
cp "$BUNDLE/.env" "$WORK/stage/opt/report-assembly-agent/stack.env.example"
cp "$ROOT/deploy/docker/package-runtime.sh" "$WORK/stage/opt/report-assembly-agent/runtime.sh"
cp "$ROOT/deploy/docker/report-assembly-agent.service" "$WORK/stage/usr/lib/systemd/system/"
chmod 755 "$WORK/stage/opt/report-assembly-agent/runtime.sh"
ln "$BUNDLE/images.tar.gz" "$WORK/stage/opt/report-assembly-agent/images.tar.gz" 2>/dev/null || cp "$BUNDLE/images.tar.gz" "$WORK/stage/opt/report-assembly-agent/images.tar.gz"
(cd "$WORK/stage/opt/report-assembly-agent" && sha256sum images.tar.gz > image.sha256)

mkdir -p "$WORK/stage/DEBIAN"
cat > "$WORK/stage/DEBIAN/control" <<EOF
Package: report-assembly-agent
Version: $VERSION
Architecture: amd64
Maintainer: Report Assembly Agent
Description: Offline report assembly application container stack
 Requires a separately installed Docker Engine and Compose plugin.
EOF
cat > "$WORK/stage/DEBIAN/preinst" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = install ] || [ "$1" = upgrade ]; then
  command -v docker >/dev/null || { echo 'Install Docker Engine first' >&2; exit 1; }
  docker info >/dev/null 2>&1 || { echo 'Start Docker Engine first' >&2; exit 1; }
  docker compose version >/dev/null 2>&1 || { echo 'Install Docker Compose plugin first' >&2; exit 1; }
fi
EOF
cat > "$WORK/stage/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = configure ]; then
  systemctl daemon-reload
  systemctl enable report-assembly-agent.service
  systemctl restart report-assembly-agent.service
fi
EOF
cat > "$WORK/stage/DEBIAN/prerm" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = remove ]; then
  systemctl disable --now report-assembly-agent.service || true
fi
EOF
cat > "$WORK/stage/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = remove ]; then systemctl daemon-reload; fi
EOF
chmod 755 "$WORK/stage/DEBIAN/"{preinst,postinst,prerm,postrm}
dpkg-deb --root-owner-group -Znone --build "$WORK/stage" "$DEB"

mkdir -p "$WORK/rpmbuild/"{BUILD,RPMS,SOURCES,SPECS,SRPMS}
ln -s "$WORK/stage" "$WORK/rpmbuild/SOURCES/payload"
cat > "$WORK/rpmbuild/SPECS/report-assembly-agent.spec" <<EOF
Name: report-assembly-agent
Version: $VERSION
Release: 1
Summary: Offline report assembly application container stack
License: Proprietary
BuildArch: x86_64
AutoReqProv: no

%description
Offline application, PostgreSQL and Qdrant container images.
Requires a separately installed Docker Engine and Compose plugin.

%prep
%build
%install
mkdir -p %{buildroot}/opt/report-assembly-agent %{buildroot}/usr/lib/systemd/system
cp -a $WORK/stage/opt/report-assembly-agent/. %{buildroot}/opt/report-assembly-agent/
cp -a $WORK/stage/usr/lib/systemd/system/. %{buildroot}/usr/lib/systemd/system/

%pre
command -v docker >/dev/null || { echo 'Install Docker Engine first' >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo 'Start Docker Engine first' >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { echo 'Install Docker Compose plugin first' >&2; exit 1; }

%post
systemctl daemon-reload
systemctl enable report-assembly-agent.service
systemctl restart report-assembly-agent.service

%preun
if [ "\$1" = 0 ]; then systemctl disable --now report-assembly-agent.service || true; fi

%postun
systemctl daemon-reload

%files
/opt/report-assembly-agent
/usr/lib/systemd/system/report-assembly-agent.service
EOF
rpmbuild -bb --define "_topdir $WORK/rpmbuild" --define '_rpmformat 4' --define '_binary_payload w0.gzdio' \
  "$WORK/rpmbuild/SPECS/report-assembly-agent.spec"
cp "$WORK/rpmbuild/RPMS/x86_64/$(basename "$RPM")" "$RPM"
(cd "$OUT" && sha256sum "$(basename "$DEB")" "$(basename "$RPM")" > SHA256SUMS)
echo "Built: $DEB"
echo "Built: $RPM"
