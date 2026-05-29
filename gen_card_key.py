#!/usr/bin/env python3
"""Generate an Ed25519 signing key for the agent card -> rookery-card-key.pem.
Serve with: mailroom_server.py --card-key rookery-card-key.pem"""
import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rookery-card-key.pem")
priv = Ed25519PrivateKey.generate()
with open(p, "wb") as fh:
    fh.write(priv.private_bytes(serialization.Encoding.PEM,
                                serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()))
os.chmod(p, 0o600)
print(f"wrote {p}  (gitignored)")
