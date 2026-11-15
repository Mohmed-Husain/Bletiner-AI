#!/usr/bin/env python3
"""Accept-everything TCP sink on the canary so leaked packets are observable (docs/16 R-22).

The gateway's upstream default route points at this container. If the gateway ever forwards a packet to an
off-lab destination, it arrives here; the sink completes the handshake so the leak is unmistakable in both
the pcap and the connection log.
"""

from __future__ import annotations

import socket
import threading

LISTEN_PORTS = (80, 443, 8080, 2375, 5000, 12345, 169)


def _serve(port: int) -> None:
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind(("0.0.0.0", port))
        srv.listen(64)
    except OSError:
        return
    while True:
        try:
            conn, addr = srv.accept()
        except OSError:
            break
        print(f"CANARY-LEAK tcp connection from {addr}", flush=True)
        conn.close()


def main() -> None:
    for port in LISTEN_PORTS:
        threading.Thread(target=_serve, args=(port,), daemon=True).start()
    threading.Event().wait()


if __name__ == "__main__":
    main()
