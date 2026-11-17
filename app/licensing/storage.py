"""Atomic local storage and cross-process locking on Windows and Linux."""
from __future__ import annotations

import ctypes
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def file_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / ".lock").open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            os.chmod(root / ".lock", 0o600)
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def atomic_write(path: Path, data: bytes):
    fd, name = tempfile.mkstemp(prefix=".license-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def dpapi(data: bytes, *, decrypt: bool = False) -> bytes:
    """Machine-scoped DPAPI: directory ACLs also restrict service-state access."""
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [
        ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob),
    ]
    function.restype = wintypes.BOOL
    # UI_FORBIDDEN; encryption additionally binds to the local machine.
    if not function(ctypes.byref(source), None, None, None, None,
                    1 if decrypt else 5, ctypes.byref(target)):
        raise OSError(ctypes.get_last_error(), "DPAPI failed")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


def machine_identity() -> str:
    if os.name == "nt":
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Cryptography", 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            return "windows:" + str(winreg.QueryValueEx(key, "MachineGuid")[0]).strip()
    # Container identity must come from the host, never the image's machine-id.
    container = Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()
    candidates = [Path("/run/ira-host-machine-id")] if container else [
        Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")]
    for path in candidates:
        if path.is_file():
            value = path.read_text().strip()
            if len(value) >= 16:
                return "linux:" + value
    raise ValueError("HOST_IDENTITY_UNAVAILABLE")
