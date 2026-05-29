#!/usr/bin/env bash
# Generate a self-signed cert for the Rookery sidecar (testing / trusted LAN).
# For the public internet, prefer a CA-signed cert or a tunnel (see README).
# Usage: ./gen_cert.sh [common-name]   (default: this box's hostname)
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
CN="${1:-$(hostname)}"
command -v openssl >/dev/null || { echo "openssl not installed"; exit 1; }
openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
  -keyout "$DIR/rookery-key.pem" -out "$DIR/rookery-cert.pem" \
  -subj "/CN=$CN" -addext "subjectAltName=DNS:$CN,DNS:localhost,IP:127.0.0.1" 2>/dev/null
chmod 600 "$DIR/rookery-key.pem"
echo "wrote rookery-cert.pem + rookery-key.pem (CN=$CN)  [gitignored]"
echo "serve: python3 mailroom_server.py --host 0.0.0.0 \\"
echo "         --tls-cert rookery-cert.pem --tls-key rookery-key.pem --token \"\$ROOKERY_TOKEN\""
