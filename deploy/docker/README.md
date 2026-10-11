# Offline CPU application bundle

This bundle contains the application, PostgreSQL, Qdrant, CPU Python/Docling
dependencies and FFmpeg. The Embedding model, Docling model artifacts, and
Whisper ASR weights are **inside the application image**, not mounted from the
host. It does **not** contain vLLM or a generation model. Configure
the generation API in administrator Settings after the application starts.

The target needs an x86_64 Linux Docker Engine and Compose plugin installed
offline beforehand. Docker installation packages vary by host distribution and
kernel; one universal Docker installer is not safe. Windows requires a separate
Docker Desktop/WSL2 installation, then the same Linux images may run under its
Linux-container backend. Test the target's Docker support before transport.

Build on a networked x86_64 Linux host:

```bash
bash deploy/docker/build-bundle.sh /path/to/empty-bundle medium
```

The build requires Docker, network access to the model repositories, and enough
disk space for CPU PyTorch, OCR/ASR model weights and three images. The single
`images.tar.gz` includes all model assets; no model directory is copied separately.
The build uses Tsinghua's Ubuntu and PyPI mirrors by default. The CPU-only
PyTorch wheels use Aliyun's PyTorch mirror because the required wheel is not
available on Tsinghua's PyPI index. Ubuntu security updates remain on the
official source. Set `IRA_PYPI_INDEX_URL` to override the Python package index
if needed; the target never needs access to any index.
The source machine's database and material files are intentionally not
included; migrate them separately when upgrading an existing installation.

On the disconnected target, edit `.env` to set a unique database password and
the desired port/UID/GID. `app.env` holds non-secret application settings. Make
`data/app` writable by `APP_UID:APP_GID`, then run:

```bash
bash install-bundle.sh
docker compose ps
curl -fsS http://127.0.0.1:8000/api/health
```

On Windows with Docker Desktop running Linux containers, edit `.env` and run
`powershell -ExecutionPolicy Bypass -File .\install-bundle.ps1` from the bundle
directory. Use a local NTFS directory rather than a network drive for the
persisted application files.

The health endpoint may report the generation model unavailable until its API
is configured. Do not use `docker compose down -v`: that removes database and
vector data. Back up `data/app` and the `pgdata` and `qdrantdata` volumes before
upgrading. This bundle is built from `ubuntu:24.04`; Ubuntu Server is not
installed in the container.
