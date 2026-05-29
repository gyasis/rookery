#!/usr/bin/env python3
"""Verify a Rookery A2A agent card's Ed25519 signature.
Usage: python3 verify_card.py <sidecar-url-or-card-url>   (exit 0 = valid)"""
import json
import os
import ssl
import sys
import urllib.request

import security

url = sys.argv[1].rstrip("/")
if not url.endswith(".json"):
    url += "/.well-known/agent-card.json"
ctx = None
if url.startswith("https") and os.environ.get("ROOKERY_INSECURE_TLS"):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
card = json.loads(urllib.request.urlopen(url, timeout=15, context=ctx).read())
ok, info = security.verify_card(card)
print(f"agent: {card.get('name')!r}  signed: {'signature' in card}")
print(f"signature: {'VALID' if ok else 'INVALID'}  ({info})")
sys.exit(0 if ok else 1)
