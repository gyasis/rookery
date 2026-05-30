#!/usr/bin/env python3
"""Node identity — manages this node's Ed25519 keypair.

Private key: ~/.rookery/id_ed25519  (PKCS8 PEM, mode 0600)
Public key:  ~/.rookery/id_ed25519.pub  (base64, mode 0644)
"""
import base64
import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_DIR = os.path.expanduser("~/.rookery")
_PRIV_PATH = os.path.join(_DIR, "id_ed25519")
_PUB_PATH = os.path.join(_DIR, "id_ed25519.pub")

_priv = None  # cached private key object


def load_or_create():
    """Idempotent. Return (priv_key, raw_pub_bytes_32).

    Creates ~/.rookery/ and a fresh Ed25519 keypair on first call.
    Subsequent calls reload from disk and return the same key.
    """
    global _priv
    os.makedirs(_DIR, mode=0o700, exist_ok=True)

    if not os.path.exists(_PRIV_PATH):
        priv = Ed25519PrivateKey.generate()
        pem = priv.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        with open(_PRIV_PATH, "wb") as fh:
            fh.write(pem)
        os.chmod(_PRIV_PATH, 0o600)

        pub_raw = priv.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        with open(_PUB_PATH, "w") as fh:
            fh.write(base64.b64encode(pub_raw).decode() + "\n")
        os.chmod(_PUB_PATH, 0o644)
        _priv = priv
    else:
        if _priv is None:
            with open(_PRIV_PATH, "rb") as fh:
                _priv = serialization.load_pem_private_key(fh.read(), password=None)

    pub_raw = _priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return _priv, pub_raw


def sign(message: bytes) -> bytes:
    """Sign message with this node's private key. Returns 64-byte signature."""
    priv, _ = load_or_create()
    return priv.sign(message)


def pubkey_b64() -> str:
    """Base64 of the raw 32-byte Ed25519 public key (no newlines)."""
    _, pub_raw = load_or_create()
    return base64.b64encode(pub_raw).decode()
