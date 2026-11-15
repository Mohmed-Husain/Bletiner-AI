"""Secret handling and redaction (DOC-14 section 6.1, Phase 7).

Redaction is pure and always-on for logs/events. The keyring-backed store is optional: if ``keyring`` is not
installed the manager uses an in-memory fallback and never writes plaintext to disk.
"""

from __future__ import annotations

from contextlib import suppress

from bletiner.models.redaction import REDACTED, redact

__all__ = ["REDACTED", "SecretStore", "redact"]


class SecretStore:
    """Thin keyring wrapper with an in-memory fallback. Never logs or returns secrets in bulk."""

    def __init__(self, service: str = "bletiner", *, use_keyring: bool = True) -> None:
        self._service = service
        self._mem: dict[str, str] = {}
        self._keyring: object | None = None
        if use_keyring:
            with suppress(Exception):  # keyring is an optional extra
                import keyring  # noqa: PLC0415 - optional dependency probed at runtime

                self._keyring = keyring

    @property
    def backend(self) -> str:
        return "keyring" if self._keyring is not None else "memory"

    def set(self, key: str, value: str) -> None:
        if self._keyring is not None:
            self._keyring.set_password(self._service, key, value)  # type: ignore[attr-defined]
        else:
            self._mem[key] = value

    def get(self, key: str) -> str | None:
        if self._keyring is not None:
            result = self._keyring.get_password(self._service, key)  # type: ignore[attr-defined]
            return str(result) if result is not None else None
        return self._mem.get(key)

    def delete(self, key: str) -> None:
        if self._keyring is not None:
            with suppress(Exception):  # deleting a missing key is not an error
                self._keyring.delete_password(self._service, key)  # type: ignore[attr-defined]
        else:
            self._mem.pop(key, None)
