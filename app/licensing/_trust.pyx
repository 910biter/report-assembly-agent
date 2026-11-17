# cython: language_level=3
"""Compiled trust root. Never load issuer keys from runtime files or settings."""

def verify(token):
    from app.licensing.protocol import verify_license
    return verify_license(token, {
        "a161220544d708c5": "AAJyPcxrKUTUBmZXxSr6OCAspBjcVkxpu83XDv2CO-c",
    })
