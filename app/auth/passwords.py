"""Versioned password hashes using the standard-library scrypt KDF."""

import hashlib
import hmac
import secrets

_N = 2**15
_R = 8
_P = 1
_DKLEN = 32


def hash_password(password: str) -> str:
    if not isinstance(password, str) or len(password) < 12:
        raise ValueError("PASSWORD_TOO_SHORT")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN,
        maxmem=64 * 1024 * 1024,
    )
    return f"scrypt-v1${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        version, n, r, p, salt_hex, digest_hex = encoded.split("$")
        if version != "scrypt-v1" or (int(n), int(r), int(p)) != (_N, _R, _P):
            return False
        salt, expected = bytes.fromhex(salt_hex), bytes.fromhex(digest_hex)
        if len(salt) != 16 or len(expected) != _DKLEN:
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P,
            dklen=_DKLEN, maxmem=64 * 1024 * 1024,
        )
        return hmac.compare_digest(actual, expected)
    except (AttributeError, TypeError, ValueError):
        return False
