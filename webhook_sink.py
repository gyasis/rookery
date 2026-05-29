#!/usr/bin/env python3
"""Tiny webhook sink for testing A2A push notifications — prints each POST body.
Usage: python3 webhook_sink.py [port]   (default 9099)"""
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode()
        print("PUSH RECEIVED: " + body, flush=True)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9099
    HTTPServer(("127.0.0.1", port), H).serve_forever()
