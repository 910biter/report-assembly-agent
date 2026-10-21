"""Internal offline issuer. Keep encrypted private keys outside the repository."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from app.licensing.protocol import PRODUCT, encode, key_id, sign, verify_request, verify_license


def main():
    parser = argparse.ArgumentParser(description="Internal offline license issuer")
    parser.add_argument("command", choices=["keygen", "issue", "inspect", "recover-clock"])
    parser.add_argument("--key", type=Path)
    parser.add_argument("--password-file", type=Path)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--generation", type=int)
    parser.add_argument("--months", type=int, choices=[3, 6, 12, 36, 60, 120],
                        help="Grant duration in calendar months (issue only; default: 3)")
    parser.add_argument("--customer", default="")
    parser.add_argument("--trust", type=Path)
    args = parser.parse_args()
    if args.months is not None and args.command != "issue":
        parser.error("--months is only valid for issue")
    if args.command == "inspect":
        grant = verify_license(args.request.read_text().strip(),
                               json.loads(args.trust.read_text()))
        args.output.write_text(grant.model_dump_json(indent=2), encoding="utf-8")
        return
    if not args.key:
        parser.error("--key is required")
    password = (args.password_file.read_bytes().strip() if args.password_file
                else getpass.getpass("Issuer key password: ").encode())
    if len(password) < 12:
        parser.error("Use a password of at least 12 characters")
    if args.command == "keygen":
        private = Ed25519PrivateKey.generate()
        args.key.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(args.key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(private.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8, serialization.BestAvailableEncryption(password)))
    else:
        private = serialization.load_pem_private_key(args.key.read_bytes(), password=password)
        if not isinstance(private, Ed25519PrivateKey):
            parser.error("Expected Ed25519 issuer key")
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    signer = key_id(public)
    if args.command == "keygen":
        with args.output.open("x", encoding="utf-8") as handle:
            json.dump({signer: encode(public)}, handle, indent=2)
        print("Created encrypted issuer key and public trust file.")
        return
    if not args.request:
        parser.error("--request is required")
    issued = int(time.time())
    if args.command == "issue":
        if not args.generation or args.generation < 1:
            parser.error("--generation must be a positive, increasing integer")
        request = verify_request(args.request.read_text().strip())
        payload = {**request.model_dump(), "kind": "grant", "key_id": signer,
                   "license_id": str(uuid4()), "generation": args.generation,
                   "issued_at": issued, "duration_months": args.months or 3}
    else:
        request = json.loads(args.request.read_text())
        payload = {"product": PRODUCT, "kind": "clock_recovery", "key_id": signer,
                   "recovery_id": str(uuid4()), "installation_id": request["installation_id"],
                   "state_hash": request["state_hash"], "issued_at": issued,
                   "valid_until": issued + 7 * 86400}
    # Append-only local issuance ledger; refuses duplicate/lower grant generations.
    from app.licensing.storage import file_lock
    with file_lock(args.key.parent):
        ledger = args.key.parent / "issuance.jsonl"
        previous = [json.loads(line) for line in ledger.read_text().splitlines()] if ledger.exists() else []
        if args.command == "issue" and any(
            row.get("installation_id") == payload["installation_id"]
            and row.get("generation", 0) >= args.generation for row in previous
        ):
            parser.error("Generation already issued; choose the next generation")
        if args.output.exists():
            parser.error("Output already exists")
        token = sign("IRA-LIC1", payload, private)
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(token + "\n")
        with ledger.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({**payload, "customer": args.customer}, ensure_ascii=False) + "\n")
        if os.name != "nt":
            os.chmod(ledger, 0o600)
    if args.command == "issue":
        months = payload["duration_months"]
        years = f" ({months // 12} years)" if months % 12 == 0 else ""
        print(f"Created {args.output}\nCustomer: {args.customer or '(not specified)'}\n"
              f"Installation: {payload['installation_id']}\nGeneration: {args.generation}\n"
              f"License term: {months} calendar months{years}\n"
              "Term starts on first successful activation of this license, not issuance.\n"
              "Renewal starts a new term; remaining time is not added.")
    else:
        print(f"Created {args.output}; clock recovery is valid for 7 days and does not extend the license term.")


if __name__ == "__main__":
    main()
