"""Real PTY bridge for the Kali sandbox terminal.

Two layers live here:

* A Qt-free terminal core: AnsiScreen (pyte screen + scrollback, ANSI -> HTML/text),
  key/paste encoders, DockerStreamDemuxer and DockerPtyTransport
  (Docker SDK exec_create/exec_start against the running Kali container).
* TerminalBridge, a thin QObject that owns a reader thread and delivers raw PTY bytes to the
  Qt thread through signals. No Qt widget code and no blocking I/O on the UI thread.

The Docker exec stream is requested with tty=True on *both* exec_create and exec_start;
Docker Desktop on Windows multiplexes the stream unless exec_start is told it is a TTY, so the
demuxer is a defensive fallback for daemons that still frame the response.
"""

from __future__ import annotations

import contextlib
import html
import logging
import re
import threading
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol

try:
    from PySide6.QtCore import QObject, Signal

    _QT_AVAILABLE = True
except ImportError:  # pragma: no cover - the UI extra is required for the card, not for tests
    _QT_AVAILABLE = False

    class QObject:  # type: ignore[no-redef]
        """Minimal QObject stand-in so the module stays importable without PySide6."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

    def Signal(*_types: Any) -> Any:  # type: ignore[no-redef]
        return None


try:
    import pyte  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - handled explicitly by AnsiScreen
    pyte = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)

DEFAULT_COLUMNS = 100
DEFAULT_LINES = 26
DEFAULT_SCROLLBACK = 800
MAX_RENDERED_SCROLLBACK = 300

DEFAULT_FG = "#e6edf3"
DEFAULT_BG = "#08090d"

#: ANSI named colours mapped onto the Bletiner palette.
ANSI_COLORS: dict[str, str] = {
    "black": "#3b4252",
    "red": "#ef4444",
    "green": "#10b981",
    "brown": "#f59e0b",
    "blue": "#38bdf8",
    "magenta": "#c084fc",
    "cyan": "#22d3ee",
    "white": "#d1d5db",
    "brightblack": "#6b7280",
    "brightred": "#f87171",
    "brightgreen": "#34d399",
    "brightbrown": "#fbbf24",
    "brightblue": "#60a5fa",
    "brightmagenta": "#e879f9",
    "brightcyan": "#67e8f9",
    "brightwhite": "#ffffff",
    "default": DEFAULT_FG,
}

_HEX_RE = re.compile(r"^[0-9a-fA-F]{6}$")
_TAG_RE = re.compile(r"<[^>]+>")
BLOCK = chr(0x2588)  # full block used to paint the cursor cell


def _html_escape(text: str) -> str:
    return html.escape(text, quote=False)


class RendererUnavailable(RuntimeError):
    """Raised when the ANSI renderer cannot be constructed (pyte missing)."""


class PtyUnavailable(RuntimeError):
    """Raised when the interactive PTY cannot be created; the message is user-facing."""


class PtyClosed(RuntimeError):
    """Raised when the PTY stream ends or the socket fails after a successful connect."""


# --------------------------------------------------------------------------------------
# Key / paste encoding
# --------------------------------------------------------------------------------------

#: Fixed escape sequences for non-printable keys.
KEY_SEQUENCES: dict[str, bytes] = {
    "Enter": b"\r",
    "Return": b"\r",
    "Backspace": b"\x7f",
    "Tab": b"\t",
    "Escape": b"\x1b",
    "Space": b" ",
    "Up": b"\x1b[A",
    "Down": b"\x1b[B",
    "Right": b"\x1b[C",
    "Left": b"\x1b[D",
    "Home": b"\x1b[H",
    "End": b"\x1b[F",
    "Insert": b"\x1b[2~",
    "Delete": b"\x1b[3~",
    "PageUp": b"\x1b[5~",
    "PageDown": b"\x1b[6~",
    "F1": b"\x1bOP",
    "F2": b"\x1bOQ",
    "F3": b"\x1bOR",
    "F4": b"\x1bOS",
    "F5": b"\x1b[15~",
    "F6": b"\x1b[17~",
    "F7": b"\x1b[18~",
    "F8": b"\x1b[19~",
    "F9": b"\x1b[20~",
    "F10": b"\x1b[21~",
    "F11": b"\x1b[23~",
    "F12": b"\x1b[24~",
    "Shift+Tab": b"\x1b[Z",
    "Ctrl+Up": b"\x1b[1;5A",
    "Ctrl+Down": b"\x1b[1;5B",
    "Ctrl+Right": b"\x1b[1;5C",
    "Ctrl+Left": b"\x1b[1;5D",
    "Ctrl+Home": b"\x1b[1;5H",
    "Ctrl+End": b"\x1b[1;5F",
    "Shift+Up": b"\x1b[1;2A",
    "Shift+Down": b"\x1b[1;2B",
    "Shift+Right": b"\x1b[1;2C",
    "Shift+Left": b"\x1b[1;2D",
}


def _control_bytes(name: str) -> bytes | None:
    """Encode Ctrl+<key> / Alt+<key> combinations."""
    if not name:
        return None
    lowered = name.lower()
    if lowered.startswith("ctrl+"):
        rest = name[5:]
        if rest.lower() == "space":
            return b"\x00"
        if len(rest) == 1:
            letter = rest.upper()
            if "A" <= letter <= "Z":
                return bytes([ord(letter) & 0x1F])
            if rest == " ":
                return b"\x00"
            if rest == "[":
                return b"\x1b"
            if rest == "\\":
                return b"\x1c"
            if rest == "]":
                return b"\x1d"
            if rest == "^":
                return b"\x1e"
            if rest == "_":
                return b"\x1f"
            if rest == "@":
                return b"\x00"
        return KEY_SEQUENCES.get(name)
    if lowered.startswith("alt+"):
        rest = name[4:]
        encoded = key_to_bytes(rest)
        return b"\x1b" + encoded if encoded else None
    return None


def key_to_bytes(key: str, modifiers: Iterable[str] = ()) -> bytes | None:
    """Translate a logical key name (+ modifier names) into the bytes a PTY expects.

    key is a printable character, a named key such as Enter/Up/F5 or a Ctrl+<x>/Shift+Tab
    combination. modifiers is an iterable of ctrl/alt/shift strings. Returns None when the key
    has no PTY encoding.
    """
    if not key:
        return None
    mods = {str(m).lower() for m in modifiers}
    if mods:
        ordered = [m for m in ("ctrl", "alt", "shift") if m in mods]
        prefix = "+".join(m.capitalize() for m in ordered)
        candidate = f"{prefix}+{key}"
        if candidate in KEY_SEQUENCES:
            return KEY_SEQUENCES[candidate]
        control = _control_bytes(candidate)
        if control is not None:
            return control
    if key in KEY_SEQUENCES:
        return KEY_SEQUENCES[key]
    encoded = _control_bytes(key)
    if encoded is not None:
        return encoded
    if len(key) == 1 and key.isprintable():
        return key.encode("utf-8")
    return None


def encode_paste(text: str, *, bracketed: bool = True) -> bytes:
    """Encode clipboard text for the PTY, wrapping it in bracketed-paste markers when requested."""
    if not text:
        return b""
    payload = text.replace("\r\n", "\n").encode("utf-8")
    if bracketed:
        return b"\x1b[200~" + payload + b"\x1b[201~"
    return payload


# --------------------------------------------------------------------------------------
# ANSI renderer
# --------------------------------------------------------------------------------------

if pyte is not None:

    class _CaptureScreen(pyte.Screen):  # type: ignore[misc]
        """pyte screen that also keeps the lines which scroll off the top of the screen."""

        def __init__(self, columns: int, lines: int, scrollback: int) -> None:
            self.scrolled: deque[tuple[Any, ...]] = deque(maxlen=max(0, scrollback))
            self.alt_screen = False
            super().__init__(columns, lines)

        def index(self) -> None:
            top, bottom = self.margins or pyte.screens.Margins(0, self.lines - 1)
            if top == 0 and not self.alt_screen and self.cursor.y == bottom:
                line = self.buffer[top]
                self.scrolled.append(tuple(line[x] for x in range(self.columns)))
            super().index()

        def set_mode(self, *modes: int, **kwargs: Any) -> None:
            super().set_mode(*modes, **kwargs)
            if kwargs.get("private") and 1049 in modes:
                self.alt_screen = True

        def reset_mode(self, *modes: int, **kwargs: Any) -> None:
            super().reset_mode(*modes, **kwargs)
            if kwargs.get("private") and 1049 in modes:
                self.alt_screen = False

        def reset(self) -> None:
            super().reset()
            self.scrolled.clear()

else:  # pragma: no cover - only when pyte is absent
    _CaptureScreen = None  # type: ignore[assignment,misc]


def _color_css(value: str, default: str) -> str:
    """Map a pyte colour token (name / 256-colour hex / 24-bit hex) to a CSS colour."""
    if not value or value == "default":
        return default
    named = ANSI_COLORS.get(value)
    if named is not None:
        return named
    if _HEX_RE.match(value):
        return f"#{value.lower()}"
    return default


@dataclass(frozen=True)
class CellStyle:
    """Resolved visual style for one terminal cell."""

    fg: str
    bg: str | None
    bold: bool = False
    italics: bool = False
    underline: bool = False
    strike: bool = False


def _resolve_style(char: Any, *, cursor: bool = False) -> CellStyle:
    fg = _color_css(char.fg, DEFAULT_FG)
    bg_raw = _color_css(char.bg, DEFAULT_BG)
    bg: str | None = None if char.bg == "default" else bg_raw
    if bool(getattr(char, "reverse", False)):
        fg, bg = bg_raw, fg
    if cursor:
        fg, bg = bg_raw, fg
    return CellStyle(
        fg=fg,
        bg=bg,
        bold=bool(getattr(char, "bold", False)),
        italics=bool(getattr(char, "italics", False)),
        underline=bool(getattr(char, "underscore", False)),
        strike=bool(getattr(char, "strikethrough", False)),
    )


def _style_css(style: CellStyle) -> str:
    parts = [f"color:{style.fg}"]
    if style.bg:
        parts.append(f"background-color:{style.bg}")
    if style.bold:
        parts.append("font-weight:700")
    if style.italics:
        parts.append("font-style:italic")
    if style.underline and style.strike:
        parts.append("text-decoration:underline line-through")
    elif style.underline:
        parts.append("text-decoration:underline")
    elif style.strike:
        parts.append("text-decoration:line-through")
    return ";".join(parts)


_DEFAULT_CELL_STYLE = CellStyle(fg=DEFAULT_FG, bg=None)


class AnsiScreen:
    """Stateful ANSI terminal renderer: bytes in, HTML/text screen out.

    Uses pyte's ByteStream/Screen for VT100 state and adds a bounded scrollback buffer. Rendering
    is run-length grouped and HTML-escaped, so shell output can never inject markup.
    """

    def __init__(
        self,
        columns: int = DEFAULT_COLUMNS,
        lines: int = DEFAULT_LINES,
        *,
        scrollback: int = DEFAULT_SCROLLBACK,
        max_render_scrollback: int = MAX_RENDERED_SCROLLBACK,
    ) -> None:
        if pyte is None or _CaptureScreen is None:
            raise RendererUnavailable(
                "pyte is not installed in the app environment; install it into the app venv first."
            )
        self._columns = max(2, columns)
        self._lines = max(2, lines)
        self._max_render_scrollback = max(0, max_render_scrollback)
        self._screen = _CaptureScreen(self._columns, self._lines, scrollback)
        self._stream = pyte.ByteStream(self._screen)
        self._scrollback_html: deque[str] = deque(maxlen=max(0, scrollback))
        self._scrolled_seen = 0
        self._text_revision = 0
        self._last_html = ""

    # -- properties ---------------------------------------------------------------
    @property
    def columns(self) -> int:
        return self._columns

    @property
    def lines(self) -> int:
        return self._lines

    @property
    def screen(self) -> Any:
        return self._screen

    @property
    def cursor(self) -> tuple[int, int, bool]:
        """Cursor (x, y, visible) in screen coordinates."""
        cur = self._screen.cursor
        x = min(max(int(cur.x), 0), self._columns - 1)
        y = min(max(int(cur.y), 0), self._lines - 1)
        return x, y, not bool(cur.hidden)

    @property
    def bracketed_paste(self) -> bool:
        """True when the shell enabled xterm bracketed paste mode (DECSET 2004)."""
        return (2004 << 5) in self._screen.mode

    @property
    def revision(self) -> int:
        """Monotonic counter increased whenever new bytes were fed."""
        return self._text_revision

    # -- feeding / sizing ---------------------------------------------------------
    def feed(self, data: bytes) -> None:
        if not data:
            return
        self._stream.feed(data)
        self._text_revision += 1

    def resize(self, columns: int, lines: int) -> None:
        columns = max(2, int(columns))
        lines = max(2, int(lines))
        if (columns, lines) == (self._columns, self._lines):
            return
        self._columns, self._lines = columns, lines
        # pyte's signature is resize(lines, columns) - note the order.
        self._screen.resize(lines=lines, columns=columns)
        self._text_revision += 1

    def clear(self) -> None:
        """Clear the visible screen and the scrollback buffer."""
        self._screen.reset()
        self._scrollback_html.clear()
        self._scrolled_seen = 0
        self._text_revision += 1

    # -- rendering ----------------------------------------------------------------
    def _sync_scrollback(self) -> None:
        scrolled = self._screen.scrolled
        if len(scrolled) < self._scrolled_seen:  # deque wrapped; oldest rows were dropped
            self._scrollback_html.clear()
            self._scrolled_seen = 0
        while self._scrolled_seen < len(scrolled):
            self._scrollback_html.append(self._render_cells(scrolled[self._scrolled_seen]))
            self._scrolled_seen += 1

    def _render_cells(self, cells: Any, *, cursor_x: int = -1) -> str:
        parts: list[str] = []
        run_text: list[str] = []
        run_style: CellStyle | None = None

        def flush() -> None:
            if not run_text:
                return
            text = _html_escape("".join(run_text))
            if run_style is None:
                parts.append(text)
            else:
                parts.append(f'<span style="{_style_css(run_style)}">{text}</span>')
            run_text.clear()

        for x in range(self._visible_width(cells, cursor_x)):
            char = cells[x]
            data = char.data
            if data == "":
                continue
            if x == cursor_x:
                data = BLOCK if not data.strip() else data
                style = _resolve_style(char, cursor=True)
            else:
                style = _resolve_style(char)
                if style == _DEFAULT_CELL_STYLE:
                    style = None
            if style != run_style:
                flush()
                run_style = style
            run_text.append(data)
        flush()
        row = "".join(parts)
        return row if row else "&#160;"

    def _visible_width(self, cells: Any, cursor_x: int) -> int:
        """Trim trailing default-blank cells so rows carry no meaningless padding."""
        end = self._columns
        while end > 0:
            index = end - 1
            if index == cursor_x:
                return end
            char = cells[index]
            if char.data == "":
                end -= 1
                continue
            blank_default = (
                char.data == " "
                and char.fg == "default"
                and char.bg == "default"
                and not any(
                    getattr(char, attr, False)
                    for attr in ("bold", "italics", "underscore", "strikethrough", "reverse")
                )
            )
            if blank_default:
                end -= 1
                continue
            break
        return end

    def render_html(self, *, pre_style: str | None = None) -> str:
        """Render scrollback + the visible screen as a preformatted HTML block.

        pre_style overrides the CSS applied to the wrapping pre element (the card uses it to pin
        the monospace font/size so the character grid matches the computed PTY size).
        """
        self._sync_scrollback()
        rows = (
            list(self._scrollback_html)[-self._max_render_scrollback :] if self._max_render_scrollback else []
        )
        cursor_x, cursor_y, cursor_visible = self.cursor
        for y in range(self._lines):
            line = self._screen.buffer[y]
            rows.append(
                self._render_cells(line, cursor_x=cursor_x if cursor_visible and y == cursor_y else -1)
            )
        style = pre_style if pre_style is not None else "margin:0; white-space:pre;"
        self._last_html = f'<pre style="{style}">' + "\n".join(rows) + "</pre>"
        return self._last_html

    def last_html(self) -> str:
        return self._last_html or self.render_html()

    def render_text(self) -> str:
        """Plain-text rendering (scrollback + visible screen) for clipboard copy and assertions."""
        self._sync_scrollback()
        rows = (
            list(self._scrollback_html)[-self._max_render_scrollback :] if self._max_render_scrollback else []
        )
        text_rows = [self._html_to_text(row) for row in rows]
        text_rows.extend(line.rstrip() for line in self._screen.display)
        return "\n".join(text_rows).rstrip("\n")

    def _html_to_text(self, row_html: str) -> str:
        text = _TAG_RE.sub("", row_html)
        return html.unescape(text).replace(chr(0xA0), " ").rstrip()

    def display_text(self) -> str:
        """Visible screen only, as plain text (used by tests and copy fallbacks)."""
        return "\n".join(line.rstrip() for line in self._screen.display)

    def contains_text(self, needle: str) -> bool:
        """True when needle appears in the visible screen or the scrollback."""
        return needle in self.render_text()


# --------------------------------------------------------------------------------------
# Docker stream framing
# --------------------------------------------------------------------------------------

_MAX_FRAME = 16 * 1024 * 1024


class DockerStreamDemuxer:
    """Strips Docker's 8-byte multiplexing frame headers when the daemon sends them.

    exec_start(..., tty=True) returns a raw TTY stream. Some daemons/transports still frame the
    response (stream id + 3 pad bytes + big-endian length); this demuxer auto-detects that and
    removes the headers, buffering partial frames across reads. Unframed streams pass through.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._framed: bool | None = None

    @property
    def framed(self) -> bool | None:
        return self._framed

    def feed(self, data: bytes) -> bytes:
        if data:
            self._buffer += data
        if self._framed is None:
            if len(self._buffer) < 8:
                return b""
            candidate = self._buffer[0] in (0, 1, 2) and self._buffer[1:4] == b"\x00\x00\x00"
            self._framed = int.from_bytes(self._buffer[4:8], "big") <= _MAX_FRAME if candidate else False
            if not self._framed:
                out = bytes(self._buffer)
                self._buffer.clear()
                return out
        if not self._framed:
            out = bytes(self._buffer)
            self._buffer.clear()
            return out

        chunks: list[bytes] = []
        while len(self._buffer) >= 8:
            if self._buffer[0] not in (0, 1, 2) or self._buffer[1:4] != b"\x00\x00\x00":
                # Corrupt header: stop framing and pass the remainder through untouched.
                self._framed = False
                chunks.append(bytes(self._buffer))
                self._buffer.clear()
                break
            size = int.from_bytes(self._buffer[4:8], "big")
            if size > _MAX_FRAME:
                self._framed = False
                chunks.append(bytes(self._buffer))
                self._buffer.clear()
                break
            if len(self._buffer) < 8 + size:
                break
            payload = bytes(self._buffer[8 : 8 + size])
            del self._buffer[: 8 + size]
            if payload:
                chunks.append(payload)
        return b"".join(chunks)

    def flush(self) -> bytes:
        """Return any buffered bytes (used when the stream ends mid-frame)."""
        out = bytes(self._buffer)
        self._buffer.clear()
        return out


# --------------------------------------------------------------------------------------
# PTY transport
# --------------------------------------------------------------------------------------

CONTAINER_NAME_HINTS = ("my-kali", "bletiner-kali", "bletiner_kali")
IMAGE_TAG_HINT = "bletiner-kali"


def _container_matches(container: Any) -> bool:
    name = str(getattr(container, "name", "") or "").lower()
    if any(hint in name for hint in CONTAINER_NAME_HINTS):
        return True
    with contextlib.suppress(Exception):
        tags = list(getattr(getattr(container, "image", None), "tags", []) or [])
        return any(IMAGE_TAG_HINT in str(tag).lower() for tag in tags)
    return False


def resolve_kali_container(client: Any) -> Any:
    """Return a running Kali container, or raise PtyUnavailable with an actionable message."""
    try:
        running = client.containers.list()
    except Exception as exc:  # docker.errors.DockerException and transport errors alike
        raise PtyUnavailable(
            f"Docker daemon unreachable ({type(exc).__name__}: {exc}). Start Docker Desktop and reconnect."
        ) from exc
    for container in running:
        if _container_matches(container):
            return container
    try:
        all_containers = client.containers.list(all=True)
    except Exception:  # daemon may vanish between the two calls
        all_containers = []
    stopped = [c for c in all_containers if _container_matches(c)]
    if stopped:
        name = str(getattr(stopped[0], "name", "my-kali"))
        raise PtyUnavailable(
            f"Kali container '{name}' is not running. Start it in Docker Desktop, then reconnect."
        )
    raise PtyUnavailable(
        "Kali sandbox container 'my-kali' was not found. Build/start the bletiner-kali container and reconnect."
    )


class PtyTransport(Protocol):
    """Duck-typed transport used by TerminalBridge (fakes implement the same methods)."""

    def open(self) -> str:
        """Create the PTY; return a short human detail string; raise on failure."""

    def read(self) -> bytes:
        """Block until output is available; return b"" on EOF; raise PtyClosed on error."""

    def write(self, data: bytes) -> None:
        """Send bytes to the PTY."""

    def resize(self, rows: int, cols: int) -> None:
        """Notify the PTY of the new character grid size (must not block the caller)."""

    def take_errors(self) -> list[str]:
        """Drain pending non-fatal transport errors (e.g. failed resize calls)."""

    def close(self) -> None:
        """Release the socket/threads; safe to call repeatedly from any thread."""


class DockerPtyTransport:
    """Interactive PTY inside the Kali container via the Docker SDK low-level API."""

    def __init__(
        self,
        container: Any = None,
        *,
        client_factory: Callable[[], Any] | None = None,
        user: str = "bletiner",
        workdir: str = "/workspace",
        shell: tuple[str, ...] = ("/bin/bash", "-l"),
        read_size: int = 65536,
    ) -> None:
        self._container = container
        self._client_factory = client_factory
        self._user = user
        self._workdir = workdir
        self._shell = shell
        self._read_size = read_size
        self._client: Any = None
        self._owns_client = False
        self._exec_id: str | None = None
        self._sock: Any = None
        self._demux = DockerStreamDemuxer()
        self._write_lock = threading.Lock()
        self._api_lock = threading.Lock()
        self._resize_lock = threading.Lock()
        self._pending_resize: tuple[int, int] | None = None
        self._resize_running = False
        self._executor: Any = None
        self._errors: deque[str] = deque(maxlen=20)
        self._closed = False
        self.detail = ""

    # -- lifecycle ---------------------------------------------------------------
    def open(self) -> str:
        if pyte is None:
            raise PtyUnavailable("pyte is not installed; the ANSI renderer is unavailable.")
        try:
            client = self._resolve_client()
            container = self._resolve_container(client)
            container_id = getattr(container, "id", None) or str(container)
            created = client.api.exec_create(
                container_id,
                cmd=list(self._shell),
                tty=True,
                stdin=True,
                stdout=True,
                stderr=True,
                user=self._user,
                workdir=self._workdir,
            )
            exec_id = str(created["Id"])
            sock = client.api.exec_start(exec_id, stream=True, socket=True, tty=True)
        except PtyUnavailable:
            raise
        except Exception as exc:
            raise PtyUnavailable(f"Could not open a PTY inside the Kali container: {exc}") from exc
        self._client = client
        self._exec_id = exec_id
        self._sock = sock
        self._closed = False
        name = str(getattr(container, "name", "kali"))
        self.detail = f"{self._user}@{name} on {self._workdir}"
        return self.detail

    def _resolve_client(self) -> Any:
        if self._client is not None:
            return self._client
        container_client = getattr(self._container, "client", None)
        if container_client is not None:
            self._client = container_client
            return container_client
        if self._client_factory is not None:
            self._client = self._client_factory()
            self._owns_client = True
            return self._client
        import docker

        self._client = docker.from_env()
        self._owns_client = True
        return self._client

    def _resolve_container(self, client: Any) -> Any:
        if self._container is not None:
            return self._container
        self._container = resolve_kali_container(client)
        return self._container

    # -- io ----------------------------------------------------------------------
    def read(self) -> bytes:
        if self._sock is None:
            raise PtyClosed("PTY socket is not open.")
        while True:
            try:
                raw = self._sock.recv(self._read_size)
            except Exception as exc:
                if self._closed:
                    return b""
                raise PtyClosed(f"PTY read failed: {exc}") from exc
            if not raw:
                return self._demux.flush()
            out = self._demux.feed(raw)
            if out:
                return out

    def write(self, data: bytes) -> None:
        if not data:
            return
        if self._closed or self._sock is None:
            raise PtyClosed("PTY is not connected.")
        with self._write_lock:
            try:
                self._sock.sendall(data)
            except Exception as exc:
                raise PtyClosed(f"PTY write failed: {exc}") from exc

    def resize(self, rows: int, cols: int) -> None:
        rows = max(2, int(rows))
        cols = max(2, int(cols))
        with self._resize_lock:
            self._pending_resize = (rows, cols)
            if self._resize_running:
                return
            self._resize_running = True
        self._control_executor().submit(self._resize_loop)

    def _control_executor(self) -> Any:
        if self._executor is None:
            from concurrent.futures import ThreadPoolExecutor

            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pty-control")
        return self._executor

    def _resize_loop(self) -> None:
        while True:
            with self._resize_lock:
                pending = self._pending_resize
                self._pending_resize = None
                if pending is None:
                    self._resize_running = False
                    return
            if self._closed or self._exec_id is None or self._client is None:
                with self._resize_lock:
                    self._resize_running = False
                return
            try:
                with self._api_lock:
                    self._client.api.exec_resize(self._exec_id, height=pending[0], width=pending[1])
            except Exception as exc:
                if not self._closed:
                    message = f"PTY resize failed: {exc}"
                    logger.warning(message)
                    self._errors.append(message)

    def take_errors(self) -> list[str]:
        errors = list(self._errors)
        self._errors.clear()
        return errors

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        threading.Thread(target=self._close_impl, name="pty-close", daemon=True).start()

    def _close_impl(self) -> None:
        sock, self._sock = self._sock, None
        if sock is not None:
            with contextlib.suppress(Exception):
                sock.close()
        if self._executor is not None:
            with contextlib.suppress(Exception):
                self._executor.shutdown(wait=False)
        if self._owns_client and self._client is not None:
            with contextlib.suppress(Exception):
                self._client.close()


# --------------------------------------------------------------------------------------
# Qt bridge
# --------------------------------------------------------------------------------------


class TerminalBridge(QObject):
    """Owns a PTY reader thread and emits events as Qt signals.

    Signals:
        data_received(object): raw bytes read from the PTY (consumed by the card renderer).
        state_changed(bool, str): connected flag + human-readable detail/reason.
    """

    data_received = Signal(object)
    state_changed = Signal(bool, str)

    def __init__(
        self,
        transport_factory: Callable[[], PtyTransport] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._transport_factory = transport_factory
        self._transport: PtyTransport | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._state_lock = threading.Lock()
        self._connected = False
        self.detail = ""

    @property
    def is_connected(self) -> bool:
        with self._state_lock:
            return self._connected

    @property
    def transport(self) -> PtyTransport | None:
        return self._transport

    def start(self) -> bool:
        """Start the reader thread; returns False when one is already running."""
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop.clear()
        self._thread = threading.Thread(target=self._reader_main, name="pty-reader", daemon=True)
        self._thread.start()
        return True

    def _reader_main(self) -> None:
        transport: PtyTransport | None = None
        final_detail = "PTY disconnected."
        try:
            transport = (
                self._transport_factory() if self._transport_factory is not None else DockerPtyTransport()
            )
            self._transport = transport
            detail = transport.open()
        except PtyUnavailable as exc:
            self._transport = None
            self._emit_state(False, str(exc))
            return
        except Exception as exc:  # defensive: never let the reader thread die silently
            logger.exception("PTY transport failed to open")
            self._transport = None
            self._emit_state(False, f"PTY unavailable: {exc}")
            return
        self.detail = detail
        self._emit_state(True, detail)
        try:
            while not self._stop.is_set():
                chunk = transport.read()
                if not chunk:
                    final_detail = "PTY stream closed by the sandbox."
                    break
                self.data_received.emit(chunk)
                for message in transport.take_errors():
                    logger.warning(message)
        except PtyClosed as exc:
            final_detail = str(exc)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("PTY reader crashed")
            final_detail = f"PTY stream error: {exc}"
        finally:
            with contextlib.suppress(Exception):
                transport.close()
            self._transport = None
            self._emit_state(False, final_detail)

    def _emit_state(self, connected: bool, detail: str) -> None:
        with self._state_lock:
            self._connected = connected
        self.detail = detail
        self.state_changed.emit(connected, detail)

    def send(self, data: bytes) -> bool:
        """Write bytes to the PTY; returns False and emits a disconnection reason on failure."""
        transport = self._transport
        if transport is None:
            return False
        try:
            transport.write(data)
        except PtyClosed as exc:
            self._emit_state(False, str(exc))
            return False
        return True

    def resize(self, rows: int, cols: int) -> None:
        transport = self._transport
        if transport is not None:
            with contextlib.suppress(Exception):
                transport.resize(rows, cols)

    def stop(self, *, join_timeout: float = 1.5) -> None:
        """Stop the reader thread and close the PTY. Never blocks longer than join_timeout."""
        self._stop.set()
        transport = self._transport
        if transport is not None:
            with contextlib.suppress(Exception):
                transport.close()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=join_timeout)
        with self._state_lock:
            self._connected = False
