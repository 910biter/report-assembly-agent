"""One authoritative offline entitlement state, independent of the business DB."""
from __future__ import annotations

import calendar
import hashlib
import hmac
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .protocol import (
    Grant, LicenseError, PRODUCT, Recovery, canonical, decode, encode, sign, verify_license,
)
from .storage import atomic_write, dpapi, file_lock, machine_identity


class LicenseBlocked(RuntimeError):
    def __init__(self, status: str):
        self.status = status
        super().__init__("LICENSE_REQUIRED")


def add_months(timestamp: int, months: int = 3) -> int:
    source = datetime.fromtimestamp(timestamp, timezone.utc)
    year, month = divmod(source.year * 12 + source.month - 1 + months, 12)
    month += 1
    day = min(source.day, calendar.monthrange(year, month)[1])
    return int(source.replace(year=year, month=month, day=day).timestamp())


class LicenseService:
    def __init__(self, root: Path, trusted_keys: dict[str, str] | None = None, *,
                 machine=machine_identity, clock=time.time, monotonic=time.monotonic):
        self.root = root
        self.keys = trusted_keys
        self.machine = machine
        self.clock = clock
        self.monotonic = monotonic
        self.lock = threading.RLock()
        self._anchor = None
        self._tick = None

    @staticmethod
    def _native():
        try:
            import importlib
            from importlib.machinery import ExtensionFileLoader
            native = importlib.import_module("app.licensing._trust")
            if not isinstance(native.__loader__, ExtensionFileLoader):
                raise ImportError("Native license module required")
        except (ImportError, OSError) as exc:
            raise LicenseError("LICENSE_MODULE_UNAVAILABLE") from exc
        return native

    def _verify(self, token):
        # Explicit keys support isolated tests; the application singleton never
        # supplies them. Production has no source or external trust-file fallback.
        if self.keys is not None:
            return verify_license(token, self.keys)
        return self._native().verify(token)

    def _identity(self) -> dict:
        machine_hash = hashlib.sha256(self.machine().encode()).hexdigest()
        path = self.root / "identity.json"
        if not path.exists():
            if (self.root / "state.json").exists():
                raise LicenseError("STATE_DAMAGED")
            seed = os.urandom(32)
            value = {"installation_id": str(uuid4()), "machine_hash": machine_hash,
                     "seed": encode(dpapi(seed) if os.name == "nt" else seed),
                     "protection": "dpapi" if os.name == "nt" else "permissions"}
            atomic_write(path, canonical(value))
        identity = json.loads(path.read_bytes())
        if identity["machine_hash"] != machine_hash:
            raise LicenseError("DEVICE_MISMATCH")
        seed = decode(identity["seed"])
        if identity["protection"] == "dpapi":
            if os.name != "nt":
                raise LicenseError("DEVICE_MISMATCH")
            seed = dpapi(seed, decrypt=True)
        identity["private_key"] = Ed25519PrivateKey.from_private_bytes(seed)
        identity["state_key"] = hashlib.sha256(b"IRA-state-v1\0" + seed).digest()
        identity["device_key"] = encode(identity["private_key"].public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw))
        return identity

    def _read(self, identity) -> dict | None:
        path = self.root / "state.json"
        if not path.exists():
            # Identity creation and initial state creation are idempotent. After
            # activation this marker prevents deleting just state.json to reset.
            if (self.root / "activated").exists():
                raise LicenseError("STATE_DAMAGED")
            return None
        envelope = json.loads(path.read_bytes())
        expected = hmac.new(identity["state_key"], canonical(envelope["state"]),
                            hashlib.sha256).hexdigest()
        if not hmac.compare_digest(envelope["mac"], expected):
            raise LicenseError("STATE_DAMAGED")
        return envelope["state"]

    def _write(self, identity, state):
        envelope = {"state": state, "mac": hmac.new(
            identity["state_key"], canonical(state), hashlib.sha256).hexdigest()}
        atomic_write(self.root / "state.json", canonical(envelope))
        atomic_write(self.root / "activated", b"1")

    def _effective_time(self, state) -> int:
        now = int(self.clock())
        tick = self.monotonic()
        high = int(state.get("high_water", 0)) if state else 0
        if self._anchor is not None:
            high = max(high, int(self._anchor + max(0, tick - self._tick)))
        if now < high - 300:
            raise LicenseError("CLOCK_ROLLBACK")
        effective = max(high, now)
        self._anchor, self._tick = effective, tick
        return effective

    def _grant(self, identity, state) -> Grant:
        grant = self._verify(state["token"])
        if not isinstance(grant, Grant):
            raise LicenseError("STATE_DAMAGED")
        self._binding(identity, grant)
        activated = state["activated_at"]
        if (not isinstance(activated, int) or activated < grant.issued_at - 300
                or state["expires_at"] != add_months(activated, grant.duration_months)):
            raise LicenseError("STATE_DAMAGED")
        return grant

    @staticmethod
    def _binding(identity, grant):
        if any(getattr(grant, name) != identity[name]
               for name in ("installation_id", "machine_hash", "device_key")):
            raise LicenseError("DEVICE_MISMATCH")

    def status(self, *, admin: bool = False) -> dict:
        try:
            if self.keys is None:
                self._native()
            with self.lock, file_lock(self.root):
                identity = self._identity()
                state = self._read(identity)
                result = {"status": "unlicensed", "active": False}
                if state:
                    grant = self._grant(identity, state)
                    now = self._effective_time(state)
                    if now - state["high_water"] >= 30:
                        state["high_water"] = now
                        self._write(identity, state)
                    active = now < state["expires_at"]
                    result.update(status="active" if active else "expired", active=active,
                                  expires_at=state["expires_at"])
                    if admin:
                        result.update(license_id=grant.license_id, generation=grant.generation,
                                      activated_at=state["activated_at"],
                                      duration_months=grant.duration_months)
                if admin:
                    result.update(installation_id=identity["installation_id"],
                                  signing_ready=True)
                return result
        except Exception as exc:
            reason = str(exc) if isinstance(exc, LicenseError) else "STATE_UNAVAILABLE"
            return {"status": reason.lower(), "active": False}

    def request(self) -> str:
        with self.lock, file_lock(self.root):
            identity = self._identity()
            body = {key: identity[key] for key in
                    ("installation_id", "device_key", "machine_hash")}
            return sign("IRA-REQ1", {"product": PRODUCT, **body}, identity["private_key"])

    def activate(self, token: str) -> dict:
        grant = self._verify(token)
        if not isinstance(grant, Grant):
            raise LicenseError("INVALID_LICENSE")
        with self.lock, file_lock(self.root):
            identity = self._identity()
            self._binding(identity, grant)
            previous = self._read(identity)
            now = self._effective_time(previous)
            if grant.issued_at > now + 300:
                raise LicenseError("CLOCK_BEFORE_ISSUE")
            if previous:
                old = self._grant(identity, previous)
                if old.license_id == grant.license_id and previous["token"] == token.strip():
                    return {"status": "unchanged", "expires_at": previous["expires_at"]}
                if (grant.license_id in previous["used_ids"] or
                        grant.generation <= old.generation):
                    raise LicenseError("LICENSE_ALREADY_USED")
            state = {"token": token.strip(), "activated_at": now,
                     "expires_at": add_months(now, grant.duration_months), "high_water": now,
                     "used_ids": (previous["used_ids"] if previous else []) + [grant.license_id],
                     "events": (previous.get("events", []) if previous else []) + [
                         {"action": "activate", "license_id": grant.license_id, "at": now}]}
            self._write(identity, state)
        return {"status": "activated", "expires_at": state["expires_at"]}

    def recovery_request(self) -> dict:
        with self.lock, file_lock(self.root):
            identity = self._identity()
            state = self._read(identity)
            if not state:
                raise LicenseError("NOT_ACTIVATED")
            self._grant(identity, state)
            return {"installation_id": identity["installation_id"],
                    "state_hash": hashlib.sha256(canonical(state)).hexdigest()}

    def recover_clock(self, token: str) -> dict:
        recovery = self._verify(token)
        if not isinstance(recovery, Recovery):
            raise LicenseError("INVALID_LICENSE")
        with self.lock, file_lock(self.root):
            identity = self._identity()
            state = self._read(identity)
            if not state:
                raise LicenseError("NOT_ACTIVATED")
            self._grant(identity, state)
            now = int(self.clock())
            if (recovery.installation_id != identity["installation_id"] or
                    recovery.state_hash != hashlib.sha256(canonical(state)).hexdigest()
                    or not recovery.issued_at - 300 <= now < recovery.valid_until):
                raise LicenseError("INVALID_RECOVERY")
            state["high_water"] = now
            state["last_recovery"] = recovery.recovery_id
            state.setdefault("events", []).append(
                {"action": "clock_recovery", "recovery_id": recovery.recovery_id, "at": now})
            self._write(identity, state)
            self._anchor, self._tick = now, self.monotonic()
        return self.status(admin=True)


_service = None
_service_lock = threading.Lock()


def get_service() -> LicenseService:
    global _service
    from app.config import settings
    root = settings.runtime_root / "license"
    with _service_lock:
        if _service is None or _service.root != root:
            _service = LicenseService(root)
        return _service


def require_license():
    status = get_service().status()
    if not status["active"]:
        raise LicenseBlocked(status["status"])


def time_monitor(stop: threading.Event):
    """Advance the high-water mark even while no browser or job is active."""
    while not stop.is_set():
        try:
            if get_service().status()["active"]:
                from app.memory.style_jobs import resume_licensed_jobs
                resume_licensed_jobs()
        except Exception:
            import logging
            logging.getLogger(__name__).exception("License monitor could not read local state")
        stop.wait(30)
