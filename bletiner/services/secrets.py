"""Secret handling and redaction (DOC-14 section 6.1, Phase 7).

Persistent backends, in priority order:

1. ``OS keyring`` -- the optional ``keyring`` package backed by Windows Credential Manager (or the
   platform equivalent). Used only when a non-failing backend is actually configured.
2. ``Windows DPAPI`` -- ``CryptProtectData`` blobs (current-user scope) written to
   ``~/.bletiner/secrets/<name>.bin``. This is the fallback for the common Windows case where the
   application runs on a bare/miniconda Python that does not ship ``keyring``.
3. ``Memory only`` -- explicit opt-out (``use_keyring=False``) or the last resort on a platform
   without DPAPI. Nothing is written to disk and the UI must say so.

The exact human-readable backend name is exposed through :attr:`SecretStore.backend` and
:func:`secret_backend_name`; the UI displays it verbatim, so a keyring is never claimed when the
runtime cannot provide one. No backend ever writes plaintext: DPAPI files are encrypted with the
current user's Windows credentials and cannot be read by another user or on another machine.

Redaction stays pure and always-on for logs/events.
"""

from __future__ import annotations

import ctypes
import os
import re
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

from bletiner.models.redaction import REDACTED, redact

__all__ = ["REDACTED", "SecretStorageError", "SecretStore", "redact", "secret_backend_name"]

BACKEND_KEYRING = "OS keyring"
BACKEND_DPAPI = "Windows DPAPI"
BACKEND_MEMORY = "Memory only"

_DPAPI_MAGIC = b"BLETINER-DPAPI-v1\n"
_CRYPTPROTECT_UI_FORBIDDEN = 0x01
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9_.-]")


# DATA_BLOB from dpapi.h: cbData DWORD + pbData BYTE*.
class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


class SecretStorageError(RuntimeError):
    """A persistent secret backend was unavailable or returned unreadable data."""


def _make_blob(data: bytes) -> tuple[_DataBlob, Any]:
    """Build a DATA_BLOB over a live Python buffer (caller keeps the buffer alive during the call)."""
    buffer = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def _local_free(pointer: Any) -> None:
    """Free a LocalAlloc'd buffer returned by DPAPI (safe on a null pointer)."""
    if isinstance(pointer, ctypes.c_void_p):
        value = pointer.value
    else:
        value = ctypes.cast(pointer, ctypes.c_void_p).value
    if not value:
        return
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    kernel32.LocalFree(ctypes.c_void_p(value))


def _crypt32() -> Any:
    if sys.platform != "win32":
        raise SecretStorageError("Windows DPAPI is only available on Windows.")
    try:
        return ctypes.WinDLL("crypt32", use_last_error=True)
    except OSError as exc:  # pragma: no cover - only reachable on damaged Windows installs
        raise SecretStorageError(f"Windows DPAPI library unavailable: {exc}") from exc


def _dpapi_protect(plaintext: bytes) -> bytes:
    """Encrypt *plaintext* for the current Windows user (CRYPTPROTECT_UI_FORBIDDEN, no prompt)."""
    crypt32 = _crypt32()
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = ctypes.c_bool
    in_blob, _keep_alive = _make_blob(plaintext)
    out_blob = _DataBlob()
    ok = crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise SecretStorageError(f"CryptProtectData failed (Windows error {ctypes.get_last_error()}).")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        _local_free(out_blob.pbData)


def _dpapi_unprotect(ciphertext: bytes) -> bytes:
    """Decrypt a blob produced by :func:`_dpapi_protect` for the current Windows user."""
    crypt32 = _crypt32()
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = ctypes.c_bool
    in_blob, _keep_alive = _make_blob(ciphertext)
    out_blob = _DataBlob()
    description = ctypes.c_void_p()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        ctypes.byref(description),
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise SecretStorageError(f"CryptUnprotectData failed (Windows error {ctypes.get_last_error()}).")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        _local_free(out_blob.pbData)
        _local_free(description)


def _load_keyring() -> Any | None:
    """Return the ``keyring`` module only when a real, non-failing backend is configured."""
    try:
        import keyring  # noqa: PLC0415 - optional dependency probed at runtime

        backend = keyring.get_keyring()
    except Exception:
        return None
    if type(backend).__module__.startswith("keyring.backends.fail"):
        return None
    try:
        if float(getattr(backend, "priority", 0)) <= 0:
            return None
    except (TypeError, ValueError):
        return None
    return keyring


class SecretStore:
    """Persistent secret store whose backend is chosen honestly and reported to the UI.

    Parameters
    ----------
    service:
        Logical namespace when the OS keyring is used.
    use_keyring:
        ``False`` explicitly disables the keyring (tests, headless runs). Combined with the default
        ``allow_dpapi=None`` this also disables DPAPI, i.e. memory-only semantics.
    base_dir:
        Override for the DPAPI blob directory (tests use a temporary directory).
    allow_dpapi:
        Force DPAPI on/off. ``None`` follows *use_keyring* so that ``use_keyring=False`` never
        writes to the user's home directory.
    """

    def __init__(
        self,
        service: str = "bletiner",
        *,
        use_keyring: bool = True,
        base_dir: Path | None = None,
        allow_dpapi: bool | None = None,
    ) -> None:
        self._service = service
        self._memory: dict[str, str] = {}
        self._keyring: Any | None = None
        self._base_dir = Path(base_dir) if base_dir is not None else Path.home() / ".bletiner" / "secrets"
        if allow_dpapi is None:
            self._dpapi_allowed = bool(use_keyring) and sys.platform == "win32"
        else:
            self._dpapi_allowed = bool(allow_dpapi) and sys.platform == "win32"
        self.note: str | None = None
        self.last_error: str | None = None
        if use_keyring:
            self._keyring = _load_keyring()
        if self._keyring is not None:
            self._backend = BACKEND_KEYRING
        elif self._dpapi_allowed:
            self._backend = BACKEND_DPAPI
        else:
            self._backend = BACKEND_MEMORY

    @property
    def backend(self) -> str:
        """Human-readable name of the backend actually in use."""
        if self._keyring is not None and self._backend == BACKEND_KEYRING:
            return BACKEND_KEYRING
        return self._backend

    def describe(self) -> str:
        """Short human sentence for the UI explaining where keys are kept right now."""
        backend = self.backend
        if backend == BACKEND_KEYRING:
            detail = "stored in the operating system credential vault"
        elif backend == BACKEND_DPAPI:
            detail = f"encrypted with Windows DPAPI (current user) in {self._base_dir}"
        else:
            detail = "kept in memory only - keys are lost when the application exits"
        if self.note:
            detail = f"{detail}. Note: {self.note}"
        return detail

    def _degrade(self, reason: str) -> None:
        if self._backend == BACKEND_KEYRING and self._dpapi_allowed:
            self._backend = BACKEND_DPAPI
            self.note = f"{reason}; switched to {BACKEND_DPAPI} so keys still persist."
        else:
            self._backend = BACKEND_MEMORY
            self.note = f"{reason}; switched to {BACKEND_MEMORY} (keys will not persist)."

    @staticmethod
    def _key_name(key: str) -> str:
        cleaned = _UNSAFE_FILENAME.sub("_", (key or "").strip())
        return cleaned or "unnamed"

    def _dpapi_path(self, name: str) -> Path:
        return self._base_dir / f"{name}.bin"

    def _dpapi_write(self, name: str, value: str) -> None:
        path = self._dpapi_path(name)
        try:
            self._base_dir.mkdir(parents=True, exist_ok=True)
            payload = _DPAPI_MAGIC + _dpapi_protect(value.encode("utf-8"))
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_bytes(payload)
            with suppress(OSError):
                os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except OSError as exc:
            raise SecretStorageError(f"Cannot write DPAPI secret file {path}: {exc}") from exc

    def _dpapi_read(self, name: str) -> str | None:
        path = self._dpapi_path(name)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise SecretStorageError(f"Cannot read DPAPI secret file {path}: {exc}") from exc
        if not raw.startswith(_DPAPI_MAGIC):
            raise SecretStorageError(
                f"{path.name} is not a Bletiner DPAPI secret (corrupt or foreign file); refusing to read it."
            )
        try:
            plaintext = _dpapi_unprotect(raw[len(_DPAPI_MAGIC) :])
        except SecretStorageError as exc:
            raise SecretStorageError(f"Cannot decrypt {path.name}: {exc}") from exc
        try:
            return plaintext.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SecretStorageError(f"Decrypted secret {path.name} is not valid UTF-8.") from exc

    def set(self, key: str, value: str) -> None:
        """Persist *value* under *key* using the active backend. Raises on unrecoverable failures."""
        name = self._key_name(key)
        if self._backend == BACKEND_KEYRING:
            try:
                self._keyring.set_password(self._service, name, value)  # type: ignore[union-attr]
                return
            except Exception as exc:
                self._degrade(f"OS keyring write failed ({exc})")
        if self._backend == BACKEND_DPAPI:
            try:
                self._dpapi_write(name, value)
                return
            except SecretStorageError as exc:
                self._degrade(f"DPAPI write failed ({exc})")
        self._memory[name] = value

    def get(self, key: str) -> str | None:
        """Return the stored secret or ``None``. Corrupt files are reported via :attr:`last_error`."""
        self.last_error = None
        name = self._key_name(key)
        if self._backend == BACKEND_KEYRING:
            try:
                result = self._keyring.get_password(self._service, name)  # type: ignore[union-attr]
                return str(result) if result is not None else None
            except Exception as exc:
                self._degrade(f"OS keyring read failed ({exc})")
        if self._backend == BACKEND_DPAPI:
            try:
                return self._dpapi_read(name)
            except SecretStorageError as exc:
                self.last_error = str(exc)
                return None
        return self._memory.get(name)

    def delete(self, key: str) -> None:
        """Remove *key* from every backend so a rotated/revoked key cannot linger anywhere."""
        name = self._key_name(key)
        self._memory.pop(name, None)
        with suppress(OSError):
            self._dpapi_path(name).unlink(missing_ok=True)
        if self._keyring is not None:
            with suppress(Exception):  # deleting a missing key is not an error
                self._keyring.delete_password(self._service, name)


def secret_backend_name() -> str:
    """Human-readable backend a default :class:`SecretStore` uses on this machine."""
    return SecretStore().backend
