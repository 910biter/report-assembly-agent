# Offline CPU application bundle

This bundle contains the application, PostgreSQL, Qdrant, CPU Python/Docling
dependencies and FFmpeg. The Embedding model, Docling model artifacts, and
Whisper ASR weights are in the environment base layer inherited by the
application image, not mounted from the host. It does **not** contain vLLM or a generation model. Configure
the generation API in administrator Settings after the application starts.

The target needs a working Docker Engine and Compose plugin. This bundle does
not install or upgrade Docker. The same x86_64 images can run on different
Linux distributions when their container runtime works. Windows requires
Docker Desktop with WSL2 and Linux containers.

For a package-manager-managed Linux installation, build two single-file
packages from the validated bundle:

```bash
bash deploy/docker/build-linux-packages.sh /path/to/bundle /path/to/empty-output 0.1.0
```

This emits one `.deb` and one `.rpm` with identical application images and a
systemd unit. They do not install Docker. On a host with working Docker Engine
and Compose, install the matching package with `sudo apt install ./...deb` or
`sudo dnf install ./...rpm`. Installation imports the images, starts the three
containers, checks the application health endpoint and enables restart at boot.
Configuration and persistent data live under `/var/lib/report-assembly-agent`;
package removal stops services but preserves that directory. This is an
x86_64-only package; there is no ARM64 image in this release.

On a fresh install, the service creates the PostgreSQL schema and a first
administrator named `admin`. Its random initial password is stored in
`/var/lib/report-assembly-agent/initial-admin-password` with root-only access.
Read it with `sudo cat /var/lib/report-assembly-agent/initial-admin-password`,
sign in, and change the password in account settings. Then configure the
generation API in administrator Settings. No generation model or previous
application data is included in the package.

Login cookies require HTTPS by default. For a private-network HTTP test,
set `IRA_AUTH_HTTP_TEST_MODE=true` in
`/var/lib/report-assembly-agent/app.env` and restart
`report-assembly-agent.service`; do not use that setting on a public network.
The package preserves this file on upgrades. It also preserves the database,
model connection, accounts, and uploaded files under its own state directory.

The build uses two images: `report-assembly-agent-base:offline` contains the
runtime, Python dependencies, OCR/embedding/ASR weights and no application
source; `report-assembly-agent:offline` adds the checked-out application source.
The final image includes the base layers when saved for an offline installer.

For the first migration, load the previous complete image from an existing
`.deb` package, then build the base from it. This reuses the downloaded
dependencies and weights instead of downloading them again:

```bash
set -o pipefail
dpkg-deb --fsys-tarfile /path/to/previous.deb \
  | tar -xO ./opt/report-assembly-agent/images.tar.gz \
  | gzip -dc | docker load
docker build --platform linux/amd64 -f deploy/docker/Dockerfile.base \
  -t report-assembly-agent-base:offline .
```

Check out the `0824` branch on an x86_64 Linux host, commit the intended
source changes, then build the application bundle:

```bash
bash deploy/docker/build-bundle.sh /path/to/empty-bundle medium
```

Once the base exists, application-only updates do not reinstall dependencies
or download weights. Building the base from the old image still needs access
to Ubuntu package repositories for small OS runtime packages. The single
`images.tar.gz` includes the final application image with all base layers;
the target never needs access to a package index.
The source machine's database and material files are intentionally not
included; migrate them separately when replacing an existing installation.
Installing the package alongside an old source checkout starts with a new,
empty database. An existing database requires an explicit backup and migration
before the old service is removed.

On the disconnected target, optionally edit `.env` for the desired port and
UID/GID. If its database password is still the placeholder, the Linux installer
generates a random one on first run. `app.env` holds non-secret settings. Run:

```bash
bash install-bundle.sh
```

For a direct bundle installation, the initial `admin` password is written to
`initial-admin-password` in the bundle directory, rather than the package
state directory. Keep that file private and change the password after login.

On Windows with Docker Desktop running Linux containers, set the password in
`.env` and run
`powershell -ExecutionPolicy Bypass -File .\install-bundle.ps1` from the bundle
directory. Use a local NTFS directory rather than a network drive for the
persisted application files.

The health endpoint may report the generation model unavailable until its API
is configured. Do not use `docker compose down -v`: that removes database and
vector data. Back up `data/app` and the `pgdata` and `qdrantdata` volumes before
upgrading. This bundle is built from `ubuntu:24.04`; Ubuntu Server is not
installed in the container.
