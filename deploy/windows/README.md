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
Qdrant Windows ZIP, and FFmpeg LGPL ZIP to `build-native-bundle.ps1`. Also pass
`-LicenseBuildPython` pointing to a build interpreter matching the bundled Python
ABI, with `tools/license-build-requirements.txt` installed and MSVC C++ Build Tools
available. License modules are compiled and their source removed from the payload;
end users do not need a compiler. The
`models` and `profile/.cache/whisper` directories must be present in the
runtime directory. The build command writes one offline ZIP when `-CreateZip`
is specified. The build machine may have uv, Git, WSL or Docker; the target
machine needs none of them.

The bundled third-party binaries should be obtained from their publishers.
Check their licenses and distribution notices before redistributing a bundle.
The build records component SHA-256 hashes in `manifest.json`; these detect
accidental corruption but are not a signed publisher provenance statement.

## Build the standard Windows installer

After generating the bundle directory (do not pass the ZIP file), download the
official LZMA SDK 26.05 from https://www.7-zip.org/sdk.html and use its
`bin/7zSD.sfx` and `bin/x64/7zr.exe` to build the single-file installer:

```powershell
.\build-installer.ps1 -BundleDirectory C:\path\to\bundle `
  -OutputDirectory C:\path\to\output `
  -SfxModule C:\path\to\lzma-sdk\bin\7zSD.sfx `
  -Archiver C:\path\to\lzma-sdk\bin\x64\7zr.exe
```

The LZMA SDK is public domain and free for commercial use. The script rejects
an SFX of 4 GiB or larger because Windows may not execute it. The installer
extracts to a temporary directory, opens a Windows installation dialog, then
uses `install-native.ps1` for all system changes. The dialog presents progress
and supports a custom program directory. It registers an uninstall entry in
Windows Apps & Features; uninstall removes program files and services while
retaining report data under ProgramData.

Before a full build, `smoke-sfx.ps1 -SfxModule <path> -Archiver <path>` checks
that the module executes the extracted PowerShell entry point. Allow enough
free space for temporary extraction and the installed runtime (at least 20 GB
on the relevant drives). This package is not code-signed; sign the finished
EXE separately for production distribution.

## Install on an offline target

Double-click `ReportAssemblyAgent-Setup-0824-x64.exe`, accept the UAC prompt,
choose the install directory, and finish the wizard. Setup registers a normal
Windows uninstall entry and Start menu shortcut. It checks the Windows build,
payload hashes, ports, and service/task conflicts before initializing the
private runtime and background processes.
If an older installation has been uninstalled but its ProgramData directory
remains, the wizard asks whether to reuse that data. Confirming creates a
protected, timestamped sibling backup before reusing the database, accounts,
reports, and configuration. Declining leaves the existing data untouched.
An active older installation must be uninstalled first; Setup never replaces
running services or silently overwrites data.

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
data survives reboots in `%ProgramData%\ReportAssemblyAgent`. For a manual
upgrade after uninstalling the old program, run `install-native.ps1 -ReuseData`
from an elevated PowerShell session. The existing PostgreSQL major version and
configured ports must match the bundle; otherwise installation stops without
modifying the database. The backup is kept after successful installation.

If startup fails, inspect `%ProgramData%\ReportAssemblyAgent\logs`. Do not
delete the data directory to retry. Restore from a backup or diagnose the
failed component first.

Uninstalling stops and removes only the `IRA-*` service/task components and
removes program files. Application data under `%ProgramData%\ReportAssemblyAgent`
is intentionally retained so reports and the database are not deleted accidentally.
