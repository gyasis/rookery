"""Mailroom bootstrap HTTP route handler.

Extracted from mailroom_server.py. Serves GET /bootstrap — either the Python
installer script (plain request) or the rookery.pyz zipapp (when ?pyz=1).

The existing bootstrap.py module (build_pyz + installer_script) is unchanged;
this file is the HTTP glue layer only.
"""

import os
import tempfile

import bootstrap

# Cache for the built zipapp bytes (rebuilt once per process lifetime).
# TODO: invalidate when source-file mtimes change.
_CACHED_PYZ = None  # bytes | None


def handle_bootstrap(handler, public_url):
    """GET /bootstrap handler.

    Args:
        handler    – BaseHTTPRequestHandler instance (for headers / wfile).
        public_url – The server's public URL (used in the installer script).
    """
    global _CACHED_PYZ
    from urllib.parse import urlparse, parse_qs

    q = parse_qs(urlparse(handler.path).query)
    if q.get("pyz"):
        # Return the zipapp bytes.
        if _CACHED_PYZ is None:
            tmp = tempfile.mktemp(suffix=".pyz")
            try:
                bootstrap.build_pyz(tmp)
                with open(tmp, "rb") as fh:
                    _CACHED_PYZ = fh.read()
            finally:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        body = _CACHED_PYZ
        handler.send_response(200)
        handler.send_header("Content-Type", "application/octet-stream")
        handler.send_header("Content-Disposition", 'attachment; filename="rookery.pyz"')
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
    else:
        # Return the Python installer script so `curl ... | python3 -` works.
        script = bootstrap.installer_script(public_url).encode()
        handler.send_response(200)
        handler.send_header("Content-Type", "text/x-python")
        handler.send_header("Content-Length", str(len(script)))
        handler.end_headers()
        handler.wfile.write(script)
