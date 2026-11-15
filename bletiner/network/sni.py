"""TLS ClientHello parsing without decryption (ADR-012; docs/16 R-26).

Stdlib only. The parser is defensive: every length is bounds-checked, and malformed input raises
``ClientHelloError`` instead of being treated as "no SNI, allow".
"""

from __future__ import annotations

from dataclasses import dataclass

TLS_HANDSHAKE = 0x16
HANDSHAKE_CLIENT_HELLO = 0x01
EXT_SERVER_NAME = 0x0000
EXT_ENCRYPTED_CLIENT_HELLO = 0xFE0D
EXT_ESNI_LEGACY = 0xFFCE
MAX_CLIENT_HELLO = 64 * 1024
RECORD_HEADER = 5


class ClientHelloError(ValueError):
    pass


@dataclass(frozen=True)
class ClientHelloInfo:
    sni: str | None
    has_ech: bool
    raw_len: int


def looks_like_tls(first: bytes) -> bool:
    return len(first) >= 1 and first[0] == TLS_HANDSHAKE


def reassemble_handshake(buf: bytes) -> bytes | None:
    """Concatenate handshake fragments from consecutive TLS records.

    Returns the complete ClientHello handshake message, or ``None`` if more bytes are needed.
    """
    payload = bytearray()
    pos = 0
    while True:
        if len(buf) - pos < RECORD_HEADER:
            return None
        ctype, ver_major = buf[pos], buf[pos + 1]
        rec_len = int.from_bytes(buf[pos + 3 : pos + 5], "big")
        if ctype != TLS_HANDSHAKE or ver_major != 3 or rec_len == 0 or rec_len > 16384 + 2048:
            raise ClientHelloError("not a TLS handshake record")
        if len(buf) - pos - RECORD_HEADER < rec_len:
            return None
        payload += buf[pos + RECORD_HEADER : pos + RECORD_HEADER + rec_len]
        pos += RECORD_HEADER + rec_len
        if len(payload) >= 4:
            if payload[0] != HANDSHAKE_CLIENT_HELLO:
                raise ClientHelloError("first handshake message is not ClientHello")
            msg_len = int.from_bytes(payload[1:4], "big")
            if msg_len > MAX_CLIENT_HELLO:
                raise ClientHelloError("ClientHello too large")
            if len(payload) >= 4 + msg_len:
                return bytes(payload[: 4 + msg_len])
        if pos > MAX_CLIENT_HELLO + 64 * RECORD_HEADER:
            raise ClientHelloError("ClientHello exceeds size limit")


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def take(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self.data):
            raise ClientHelloError("truncated ClientHello")
        out = self.data[self.pos : self.pos + n]
        self.pos += n
        return out

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return int.from_bytes(self.take(2), "big")

    def vec8(self) -> bytes:
        return self.take(self.u8())

    def vec16(self) -> bytes:
        return self.take(self.u16())

    def done(self) -> bool:
        return self.pos == len(self.data)


def parse_client_hello(handshake: bytes) -> ClientHelloInfo:
    """Parse a complete ClientHello handshake message (as returned by :func:`reassemble_handshake`)."""
    r = _Reader(handshake)
    if r.u8() != HANDSHAKE_CLIENT_HELLO:
        raise ClientHelloError("not a ClientHello")
    body_len = int.from_bytes(r.take(3), "big")
    body = _Reader(r.take(body_len))
    body.take(2)  # legacy_version
    body.take(32)  # random
    body.vec8()  # legacy_session_id
    suites = body.vec16()
    if len(suites) % 2 or not suites:
        raise ClientHelloError("bad cipher_suites")
    body.vec8()  # compression methods
    sni: str | None = None
    has_ech = False
    seen: set[int] = set()
    if not body.done():
        exts = _Reader(body.vec16())
        while not exts.done():
            etype = exts.u16()
            edata = exts.vec16()
            if etype in seen:
                raise ClientHelloError(f"duplicate extension {etype:#06x}")  # RFC 8446 4.2
            seen.add(etype)
            if etype in (EXT_ENCRYPTED_CLIENT_HELLO, EXT_ESNI_LEGACY):
                has_ech = True
            elif etype == EXT_SERVER_NAME:
                sni = _parse_server_name(edata)
    if not body.done():
        raise ClientHelloError("trailing bytes after extensions")
    return ClientHelloInfo(sni=sni, has_ech=has_ech, raw_len=len(handshake))


def _parse_server_name(data: bytes) -> str:
    r = _Reader(data)
    lst = _Reader(r.vec16())
    if not r.done():
        raise ClientHelloError("trailing bytes in server_name")
    names: list[str] = []
    while not lst.done():
        name_type = lst.u8()
        name = lst.vec16()
        if name_type == 0:
            try:
                names.append(name.decode("ascii"))
            except UnicodeDecodeError as exc:
                raise ClientHelloError("non-ASCII SNI") from exc
    # RFC 6066: at most one host_name. Multiple names are an evasion attempt.
    if len(names) != 1 or not names[0]:
        raise ClientHelloError("server_name must contain exactly one host_name")
    return names[0]
