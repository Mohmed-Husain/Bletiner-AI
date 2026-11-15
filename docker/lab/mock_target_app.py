#!/usr/bin/env python3
"""Deliberately simple local lab target (docs/16 section 3). NEVER a real service, NEVER public.

Serves, on a single IP (198.51.100.10):
  * HTTPS :443 - TLS with a self-signed cert whose SAN covers BOTH ``local-lab.test`` and
    ``unauthorized-tenant.test`` (shared-CDN / multi-tenant simulation). Honours SNI.
  * HTTP :80  - routes by ``Host`` header; emits a 302 to an out-of-scope domain at ``/redirect-external``.

The dedicated-IP target (198.51.100.20) runs the same app bound to TCP 8443/9999 for raw-forwarding tests.
"""

from __future__ import annotations

import http.server
import os
import ssl
import sys

REDIRECT_TARGET = "https://unauthorized-destination.test/stolen"


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _body(self, status: int, text: str) -> None:
        payload = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        host = self.headers.get("Host", "").split(":")[0]
        if self.path == "/redirect-external":
            self.send_response(302)
            self.send_header("Location", REDIRECT_TARGET)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            return
        self._body(200, f"bletiner-lab-target host={host} path={self.path}\n")

    do_HEAD = do_GET

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("lab-target " + (fmt % args) + "\n")


def main() -> int:
    mode = os.environ.get("LAB_MODE", "web")
    cert = os.environ.get("LAB_CERT", "/certs/lab.pem")
    if mode == "dedicated":
        # Dedicated-IP target: plain TCP listeners on 8443 + 9999 for raw forwarding tests.
        import socketserver

        port = int(os.environ.get("LAB_PORT", "8443"))
        with socketserver.TCPServer(("0.0.0.0", port), Handler) as httpd:
            httpd.serve_forever()
        return 0

    http_srv = http.server.ThreadingHTTPServer(("0.0.0.0", 80), Handler)
    https_srv = http.server.ThreadingHTTPServer(("0.0.0.0", 443), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert)
    https_srv.socket = ctx.wrap_socket(https_srv.socket, server_side=True)

    import threading

    threading.Thread(target=http_srv.serve_forever, daemon=True).start()
    https_srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
