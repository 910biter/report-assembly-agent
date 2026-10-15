# Native Windows offline bundle

This is a Windows 10/11 x64 CPU application package. It does not install or
use Docker, WSL, a system Python, or a local generation model. Configure a
vLLM-compatible generation endpoint in administrator Settings after install.
The standard distribution is a single Setup EXE built from this bundle.

The bundle contains a private CPython 3.12, pinned Windows wheels, PostgreSQL
16, Qdrant 1.13.6, FFmpeg, Docling/OCR, embedding and Whisper model artifacts,
the application source, and a PowerShell installer. PostgreSQL and Qdrant only
listen on loopback. The application listens on all interfaces; Setup adds an
inbound firewall rule for the application port without a source-address
restriction. The network operator must control reachability. HTTP login is
unencrypted; use HTTPS before exposing the port beyond a trusted network.

## Build on a connected Windows machine

Run `build-python-runtime.ps1` first to produce a private interpreter, lock
file, and offline wheelhouse. Supply the Windows PostgreSQL binary ZIP,
Qdrant Windows ZIP, and FFmpeg LGPL ZIP to `build-native-bundle.ps1`. The
`models` and `profile/.cache/whisper` directories must be present in the
runtime directory. The build command writes one offline ZIP when `-CreateZip`
is specified. The build machine may have uv, Git, WSL or Docker; the target
machine needs none of them.

The bundled third-party binaries should be obtained from their publishers.
Check their licenses and distribution notices before redistributing a bundle.
The build records component SHA-256 hashes in `manifest.json`; these detect
accidental corruption but are not a signed publisher provenance statement.

## Build the standard Windows installer

After generating the bundle directory (do not pass the ZIP file), compile the
single-file wizard installer with Inno Setup 7 x64:

```powershell
.\build-installer.ps1 -BundleDirectory C:\path\to\bundle -OutputDirectory C:\path\to\output
```

The Inno Setup publisher requests a commercial license for commercial and
in-house business use. Decide licensing before distributing the installer.

## Install on an offline target

Double-click `ReportAssemblyAgent-Setup-0824-x64.exe`, accept the UAC prompt,
choose the install directory, and finish the wizard. Setup registers a normal
Windows uninstall entry and Start menu shortcut. It checks the Windows build,
payload hashes, ports, and service/task conflicts before initializing the
private runtime and background processes.

The standalone bundle ZIP remains available for manual/offline diagnostics:
Extract the ZIP to local NTFS storage and double-click `Install.cmd`. Accept
the administrator elevation prompt. The installer checks checksums and ports,
copies private binaries to `Program Files`, puts mutable state under
`ProgramData`, installs PostgreSQL as a Windows service, and registers Qdrant
and the application as startup tasks running as SYSTEM. It waits for all
health endpoints before declaring success. On first install it creates an
`admin` account and writes the random initial password to
`%ProgramData%\ReportAssemblyAgent\initial-admin-password` (administrators
only). Change that password after first login.

Default ports are 8000 (application), 15432 (PostgreSQL), and 16333
(Qdrant).
The application can be opened from another device at `http://<host-ip>:8000`
when the network permits it. The host firewall allows inbound connections to
this port from any source; it does not expose the database or vector store.
The application firewall rule is removed on uninstall.
No system Python registration or PATH change is made. Persistent
data survives reboots in `%ProgramData%\ReportAssemblyAgent`. The source
bundle is for a **fresh installation**; it intentionally rejects an existing
installation instead of overwriting a database. Back up and migrate existing
data explicitly before replacing an older installation.

If startup fails, inspect `%ProgramData%\ReportAssemblyAgent\logs`. Do not
delete the data directory to retry. Restore from a backup or diagnose the
failed component first.

Uninstalling stops and removes only the `IRA-*` service/task components and
removes program files. Application data under `%ProgramData%\ReportAssemblyAgent`
is intentionally retained so reports and the database are not deleted accidentally.
