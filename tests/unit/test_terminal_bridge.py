"""Unit tests for the real PTY terminal bridge and card.

No Docker daemon, container or network is required: the Docker transport is exercised against a
fake client and the Qt bridge against an in-memory socketpair transport.
"""

from __future__ import annotations

import contextlib
import os
import re
import socket
import threading
import time
import unicodedata
from types import SimpleNamespace
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from bletiner.ui import terminal_bridge
from bletiner.ui.terminal_bridge import (
    AnsiScreen,
    DockerPtyTransport,
    DockerStreamDemuxer,
    PtyClosed,
    PtyUnavailable,
    RendererUnavailable,
    TerminalBridge,
    encode_paste,
    key_to_bytes,
    resolve_kali_container,
)

try:
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    QT_AVAILABLE = True
except ImportError:  # pragma: no cover - the ui extra is expected in this project
    QT_AVAILABLE = False
    QApplication = None  # type: ignore[assignment]


def pump(app: Any, predicate: Any, timeout: float = 5.0) -> bool:
    """Process Qt events until predicate() is true or the timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture(scope="module")
def qapp() -> Any:
    if not QT_AVAILABLE:
        pytest.skip("PySide6 not installed")
    app = QApplication.instance() or QApplication([])
    yield app


# --------------------------------------------------------------------------------------
# ANSI renderer
# --------------------------------------------------------------------------------------


class TestAnsiScreen:
    def test_plain_text_and_display(self) -> None:
        screen = AnsiScreen(40, 6)
        screen.feed(b"hello\r\nworld")
        assert "hello" in screen.render_text()
        assert "world" in screen.display_text()

    def test_html_escaping_blocks_markup_injection(self) -> None:
        screen = AnsiScreen(60, 4)
        payload = b"<script>alert(1)</script> & <img src=x>"
        screen.feed(payload)
        rendered = screen.render_html()
        assert "<script>" not in rendered
        assert "&lt;script&gt;" in rendered
        assert "&amp;" in rendered
        assert "<script>alert(1)</script>" in screen.render_text()

    def test_ansi_color_is_rendered(self) -> None:
        screen = AnsiScreen(60, 4)
        screen.feed(b"\x1b[31mRED\x1b[0m plain")
        html = screen.render_html()
        assert "#ef4444" in html
        assert "RED" in html
        assert "plain" in html
        # Default-styled text is emitted without a colour span.
        after_reset = html.split("plain", 1)[0]
        assert "font-weight:700" not in after_reset

    def test_256_and_truecolor_palette(self) -> None:
        screen = AnsiScreen(40, 4)
        screen.feed(b"\x1b[38;5;196mX\x1b[48;2;1;2;3mY")
        html = screen.render_html()
        assert "#ff0000" in html
        assert "#010203" in html

    def test_bold_underline_italics(self) -> None:
        screen = AnsiScreen(40, 4)
        screen.feed(b"\x1b[1mB\x1b[4mU\x1b[3mI\x1b[0m")
        html = screen.render_html()
        assert "font-weight:700" in html
        assert "text-decoration:underline" in html
        assert "font-style:italic" in html

    def test_cursor_block_rendered_and_position(self) -> None:
        screen = AnsiScreen(40, 4)
        screen.feed(b"abc")
        x, y, visible = screen.cursor
        assert (x, y, visible) == (3, 0, True)
        html = screen.render_html()
        assert terminal_bridge.BLOCK in html
        row_with_cursor = next(line for line in html.splitlines() if terminal_bridge.BLOCK in line)
        assert "abc" in row_with_cursor

    def test_scrollback_keeps_old_lines(self) -> None:
        screen = AnsiScreen(20, 3)
        for index in range(6):
            screen.feed(f"line{index}\r\n".encode())
        text = screen.render_text()
        assert "line0" in text
        assert "line5" in text
        visible = screen.display_text()
        assert "line0" not in visible

    def test_scrollback_buffer_is_bounded(self) -> None:
        screen = AnsiScreen(10, 2, scrollback=4, max_render_scrollback=50)
        for index in range(50):
            screen.feed(f"r{index}\r\n".encode())
        text = screen.render_text()
        assert "r49" in text
        assert len(text.splitlines()) <= 6 + 2
        assert len(screen._scrollback_html) <= 4

    def test_resize_changes_grid_and_wraps(self) -> None:
        screen = AnsiScreen(40, 6)
        screen.resize(12, 3)
        assert screen.columns == 12
        assert screen.lines == 3
        screen.feed(b"abcdefghijklmnopqrstuvwxyz")
        assert len(screen.display_text().splitlines()) == 3
        assert "abc" in screen.display_text()

    def test_resize_preserves_existing_screen(self) -> None:
        screen = AnsiScreen(20, 4)
        screen.feed(b"keep me")
        screen.resize(30, 6)
        assert "keep me" in screen.display_text()

    def test_bracketed_paste_mode(self) -> None:
        screen = AnsiScreen(20, 3)
        assert screen.bracketed_paste is False
        screen.feed(b"\x1b[?2004h")
        assert screen.bracketed_paste is True
        screen.feed(b"\x1b[?2004l")
        assert screen.bracketed_paste is False

    def test_clear_empties_screen_and_scrollback(self) -> None:
        screen = AnsiScreen(20, 3)
        for index in range(10):
            screen.feed(f"old{index}\r\n".encode())
        screen.clear()
        assert "old0" not in screen.render_text()
        assert screen.cursor == (0, 0, True)

    def test_wide_character_not_corrupted(self) -> None:
        screen = AnsiScreen(20, 3)
        screen.feed("中文".encode())
        text = screen.render_text()
        assert "中" in text
        assert "文" in text

    def test_zero_width_combining_char_merges(self) -> None:
        screen = AnsiScreen(20, 3)
        screen.feed(("e" + chr(0x301)).encode())
        text = screen.render_text().strip()
        # pyte normalizes combining marks into the previous cell (NFC), so the cluster stays intact.
        assert text == unicodedata.normalize("NFC", "e" + chr(0x301))
        assert screen.cursor[0] == 1

    def test_erase_display_and_home_sequences(self) -> None:
        screen = AnsiScreen(20, 3)
        screen.feed(b"abc\r\ndef\r\n\x1b[2J\x1b[H")
        assert screen.display_text().strip() == ""
        assert screen.cursor == (0, 0, True)

    def test_wide_character_occupies_one_cell_of_text(self) -> None:
        screen = AnsiScreen(10, 2)
        screen.feed("中文".encode())
        text = screen.render_text()
        assert text.count("中") == 1
        assert text.count("文") == 1
        assert screen.cursor[0] == 4

    def test_render_html_pre_style_override(self) -> None:
        screen = AnsiScreen(20, 3)
        assert 'style="margin:0"' in screen.render_html(pre_style="margin:0")

    def test_empty_rows_have_height(self) -> None:
        screen = AnsiScreen(20, 3)
        screen.feed(b"top")
        assert "&#160;" in screen.render_html()

    def test_missing_pyte_raises_renderer_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(terminal_bridge, "pyte", None)
        with pytest.raises(RendererUnavailable):
            AnsiScreen(20, 3)


# --------------------------------------------------------------------------------------
# Key / paste encoding
# --------------------------------------------------------------------------------------


class TestKeyEncoding:
    @pytest.mark.parametrize(
        ("key", "expected"),
        [
            ("Enter", b"\r"),
            ("Backspace", b"\x7f"),
            ("Tab", b"\t"),
            ("Escape", b"\x1b"),
            ("Up", b"\x1b[A"),
            ("Down", b"\x1b[B"),
            ("Right", b"\x1b[C"),
            ("Left", b"\x1b[D"),
            ("Home", b"\x1b[H"),
            ("End", b"\x1b[F"),
            ("PageUp", b"\x1b[5~"),
            ("PageDown", b"\x1b[6~"),
            ("Insert", b"\x1b[2~"),
            ("Delete", b"\x1b[3~"),
            ("F1", b"\x1bOP"),
            ("F5", b"\x1b[15~"),
            ("F12", b"\x1b[24~"),
            ("Shift+Tab", b"\x1b[Z"),
            ("Ctrl+Up", b"\x1b[1;5A"),
            ("Shift+Left", b"\x1b[1;2D"),
        ],
    )
    def test_named_keys(self, key: str, expected: bytes) -> None:
        assert key_to_bytes(key) == expected

    @pytest.mark.parametrize(
        ("key", "expected"),
        [
            ("Ctrl+A", b"\x01"),
            ("Ctrl+C", b"\x03"),
            ("Ctrl+D", b"\x04"),
            ("Ctrl+E", b"\x05"),
            ("Ctrl+L", b"\x0c"),
            ("Ctrl+U", b"\x15"),
            ("Ctrl+W", b"\x17"),
            ("Ctrl+Z", b"\x1a"),
            ("Ctrl+Space", b"\x00"),
        ],
    )
    def test_control_keys(self, key: str, expected: bytes) -> None:
        assert key_to_bytes(key) == expected

    def test_control_modifier_argument(self) -> None:
        assert key_to_bytes("C", ["ctrl"]) == b"\x03"
        assert key_to_bytes("d", ["ctrl"]) == b"\x04"
        assert key_to_bytes("L", modifiers=("ctrl",)) == b"\x0c"

    def test_alt_prefix(self) -> None:
        assert key_to_bytes("x", ["alt"]) == b"\x1bx"
        assert key_to_bytes("Up", ["ctrl"]) == b"\x1b[1;5A"

    def test_printable_and_unicode(self) -> None:
        assert key_to_bytes("a") == b"a"
        assert key_to_bytes("Z") == b"Z"
        assert key_to_bytes("é") == "é".encode()

    def test_unknown_and_empty_return_none(self) -> None:
        assert key_to_bytes("NoSuchKey") is None
        assert key_to_bytes("") is None

    def test_paste_bracketed_and_plain(self) -> None:
        assert encode_paste("a\nb") == b"\x1b[200~a\nb\x1b[201~"
        assert encode_paste("a\nb", bracketed=False) == b"a\nb"
        assert encode_paste("") == b""
        assert encode_paste("line1\r\nline2", bracketed=False) == b"line1\nline2"


# --------------------------------------------------------------------------------------
# Docker stream framing
# --------------------------------------------------------------------------------------


def _frame(stream: int, payload: bytes) -> bytes:
    return bytes([stream, 0, 0, 0]) + len(payload).to_bytes(4, "big") + payload


class TestDockerStreamDemuxer:
    def test_framed_stream_is_unwrapped(self) -> None:
        demux = DockerStreamDemuxer()
        data = _frame(1, b"stdout line\r\n") + _frame(2, b"stderr line\r\n")
        assert demux.feed(data) == b"stdout line\r\nstderr line\r\n"
        assert demux.framed is True

    def test_framed_stream_across_partial_reads(self) -> None:
        demux = DockerStreamDemuxer()
        data = _frame(1, b"chunk one ") + _frame(1, b"chunk two")
        out = b""
        for index in range(0, len(data), 3):
            out += demux.feed(data[index : index + 3])
        assert out == b"chunk one chunk two"

    def test_raw_tty_stream_passes_through(self) -> None:
        demux = DockerStreamDemuxer()
        payload = b"$ echo hi\r\nhi\r\n"
        assert demux.feed(payload) == payload
        assert demux.framed is False

    def test_corrupt_header_falls_back_to_raw(self) -> None:
        demux = DockerStreamDemuxer()
        assert demux.feed(_frame(1, b"ok")) == b"ok"
        raw = demux.feed(b"\x05garbage without a frame header")
        assert raw == b"\x05garbage without a frame header"

    def test_flush_returns_partial_buffer(self) -> None:
        demux = DockerStreamDemuxer()
        demux.feed(_frame(1, b"trailing frame")[:10])
        assert demux.flush() == _frame(1, b"trailing frame")[:10]


# --------------------------------------------------------------------------------------
# Container resolution (fake Docker client, no daemon)
# --------------------------------------------------------------------------------------


class _FakeContainers:
    def __init__(
        self, running: list[Any], stopped: list[Any] | None = None, error: Exception | None = None
    ) -> None:
        self._running = running
        self._stopped = stopped or []
        self._error = error

    def list(self, all: bool = False) -> list[Any]:
        if self._error is not None:
            raise self._error
        return self._stopped if all else self._running


class _FakeClient:
    def __init__(self, containers: _FakeContainers) -> None:
        self.containers = containers


def _container(name: str, tags: tuple[str, ...] = ()) -> SimpleNamespace:
    return SimpleNamespace(name=name, image=SimpleNamespace(tags=list(tags)), id=f"id-{name}")


class TestContainerResolution:
    def test_finds_running_kali_container(self) -> None:
        kali = _container("my-kali")
        client = _FakeClient(_FakeContainers([_container("unrelated"), kali]))
        assert resolve_kali_container(client) is kali

    def test_matches_by_image_tag(self) -> None:
        kali = _container("random-name", ("bletiner-kali:latest",))
        client = _FakeClient(_FakeContainers([kali]))
        assert resolve_kali_container(client) is kali

    def test_stopped_container_is_actionable(self) -> None:
        client = _FakeClient(_FakeContainers([], [_container("my-kali")]))
        with pytest.raises(PtyUnavailable, match="is not running"):
            resolve_kali_container(client)

    def test_missing_container_is_actionable(self) -> None:
        client = _FakeClient(_FakeContainers([]))
        with pytest.raises(PtyUnavailable, match="was not found"):
            resolve_kali_container(client)

    def test_docker_down_is_actionable(self) -> None:
        client = _FakeClient(_FakeContainers([], error=RuntimeError("Cannot connect to Docker daemon")))
        with pytest.raises(PtyUnavailable, match="Docker daemon unreachable"):
            resolve_kali_container(client)


# --------------------------------------------------------------------------------------
# DockerPtyTransport argument contract (fake Docker client/socket)
# --------------------------------------------------------------------------------------


class _FakeSocket:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)
        self.sent = bytearray()
        self.closed = False

    def recv(self, _size: int) -> bytes:
        if self.closed:
            return b""
        return self._chunks.pop(0) if self._chunks else b""

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)

    def close(self) -> None:
        self.closed = True


class _FakeExecApi:
    def __init__(self, sock: _FakeSocket) -> None:
        self.sock = sock
        self.created: tuple[Any, dict[str, Any]] | None = None
        self.started: tuple[Any, dict[str, Any]] | None = None
        self.resizes: list[tuple[Any, Any]] = []

    def exec_create(self, container_id: Any, **kwargs: Any) -> dict[str, str]:
        self.created = (container_id, kwargs)
        return {"Id": "exec-123"}

    def exec_start(self, exec_id: Any, **kwargs: Any) -> _FakeSocket:
        self.started = (exec_id, kwargs)
        return self.sock

    def exec_resize(self, exec_id: Any, height: Any = None, width: Any = None) -> None:
        self.resizes.append((height, width))


class _FakeDockerClient:
    def __init__(self, sock: _FakeSocket) -> None:
        self.api = _FakeExecApi(sock)

    def close(self) -> None:
        self.closed = True


def _wait_until(predicate: Any, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class TestDockerPtyTransport:
    def test_open_uses_tty_exec_contract(self) -> None:
        sock = _FakeSocket([b"prompt$ "])
        client = _FakeDockerClient(sock)
        transport = DockerPtyTransport(
            container=SimpleNamespace(id="cid", name="my-kali"),
            client_factory=lambda: client,
            user="bletiner",
            workdir="/workspace",
        )
        detail = transport.open()
        assert "my-kali" in detail and "bletiner" in detail
        container_id, create_kwargs = client.api.created
        assert container_id == "cid"
        assert create_kwargs["tty"] is True
        assert create_kwargs["stdin"] is True
        assert create_kwargs["user"] == "bletiner"
        assert create_kwargs["workdir"] == "/workspace"
        assert create_kwargs["cmd"] == ["/bin/bash", "-l"]
        exec_id, start_kwargs = client.api.started
        assert exec_id == "exec-123"
        assert start_kwargs["tty"] is True
        assert start_kwargs["socket"] is True
        assert start_kwargs["stream"] is True
        assert transport.read() == b"prompt$ "
        transport.write(b"echo hi\r")
        assert bytes(sock.sent) == b"echo hi\r"
        transport.resize(40, 120)
        assert _wait_until(lambda: client.api.resizes == [(40, 120)])
        transport.close()
        assert _wait_until(lambda: sock.closed)
        assert _wait_until(lambda: transport.take_errors() == [])

    def test_read_failure_raises_pty_closed(self) -> None:
        class BrokenSocket(_FakeSocket):
            def recv(self, _size: int) -> bytes:
                raise OSError("pipe broken")

        transport = DockerPtyTransport(
            container=SimpleNamespace(id="cid", name="my-kali"),
            client_factory=lambda: _FakeDockerClient(BrokenSocket([])),
        )
        transport.open()
        with pytest.raises(PtyClosed, match="PTY read failed"):
            transport.read()

    def test_write_after_close_raises(self) -> None:
        sock = _FakeSocket([])
        transport = DockerPtyTransport(
            container=SimpleNamespace(id="cid", name="my-kali"),
            client_factory=lambda: _FakeDockerClient(sock),
        )
        transport.open()
        transport.close()
        assert _wait_until(lambda: transport._sock is None)
        assert _wait_until(lambda: sock.closed)
        with pytest.raises(PtyClosed):
            transport.write(b"x")


# --------------------------------------------------------------------------------------
# TerminalBridge with an in-memory socketpair transport
# --------------------------------------------------------------------------------------


class _FakePtyTransport:
    def __init__(self, *, open_error: Exception | None = None, read_error: Exception | None = None) -> None:
        self.host, self.peer = socket.socketpair()
        self.host.settimeout(0.05)
        self.peer.settimeout(3.0)
        self.open_error = open_error
        self.read_error = read_error
        self.resizes: list[tuple[int, int]] = []
        self.closed = threading.Event()

    def open(self) -> str:
        if self.open_error is not None:
            raise self.open_error
        return "bletiner@fake-kali"

    def read(self) -> bytes:
        if self.read_error is not None:
            raise self.read_error
        while not self.closed.is_set():
            try:
                data = self.host.recv(65536)
            except TimeoutError:
                continue
            except OSError:
                return b""
            return data
        return b""

    def write(self, data: bytes) -> None:
        if self.closed.is_set():
            raise PtyClosed("closed")
        self.host.sendall(data)

    def resize(self, rows: int, cols: int) -> None:
        self.resizes.append((rows, cols))

    def take_errors(self) -> list[str]:
        return []

    def close(self) -> None:
        if self.closed.is_set():
            return
        self.closed.set()
        self.host.close()
        self.peer.close()


@pytest.mark.skipif(not QT_AVAILABLE, reason="PySide6 not installed")
class TestTerminalBridge:
    def test_fake_socket_round_trip(self, qapp: Any) -> None:
        transport = _FakePtyTransport()
        bridge = TerminalBridge(lambda: transport)
        states: list[tuple[bool, str]] = []
        received = bytearray()
        bridge.state_changed.connect(lambda connected, detail: states.append((connected, detail)))
        bridge.data_received.connect(lambda chunk: received.extend(bytes(chunk)))
        assert bridge.start() is True
        assert pump(qapp, lambda: bridge.is_connected)
        assert states[0] == (True, "bletiner@fake-kali")

        assert bridge.send(b"echo hi\r") is True
        transport.peer.settimeout(3.0)
        assert transport.peer.recv(64) == b"echo hi\r"

        transport.peer.sendall(b"from the pty\r\n")
        assert pump(qapp, lambda: b"from the pty" in received)
        bridge.resize(30, 90)
        assert transport.resizes == [(30, 90)]

        bridge.stop()
        assert transport.closed.is_set()
        assert bridge.is_connected is False
        assert pump(qapp, lambda: states[-1][0] is False)
        assert states[-1] == (False, "PTY stream closed by the sandbox.")

    def test_reader_error_disconnects(self, qapp: Any) -> None:
        transport = _FakePtyTransport()
        bridge = TerminalBridge(lambda: transport)
        states: list[tuple[bool, str]] = []
        bridge.state_changed.connect(lambda connected, detail: states.append((connected, detail)))
        bridge.start()
        assert pump(qapp, lambda: bridge.is_connected)
        transport.peer.close()
        assert pump(qapp, lambda: len(states) >= 2 and states[-1][0] is False)
        bridge.stop()

    def test_offline_transport_is_reported(self, qapp: Any) -> None:
        bridge = TerminalBridge(lambda: _FakePtyTransport(open_error=PtyUnavailable("Docker offline (test)")))
        states: list[tuple[bool, str]] = []
        bridge.state_changed.connect(lambda connected, detail: states.append((connected, detail)))
        bridge.start()
        assert pump(qapp, lambda: bool(states))
        assert states == [(False, "Docker offline (test)")]
        assert bridge.is_connected is False
        assert bridge.send(b"x") is False
        bridge.stop()

    def test_unexpected_open_error_is_reported(self, qapp: Any) -> None:
        bridge = TerminalBridge(lambda: _FakePtyTransport(open_error=RuntimeError("boom")))
        states: list[tuple[bool, str]] = []
        bridge.state_changed.connect(lambda connected, detail: states.append((connected, detail)))
        bridge.start()
        assert pump(qapp, lambda: bool(states))
        assert states[0][0] is False
        assert "boom" in states[0][1]
        bridge.stop()

    def test_stop_is_idempotent(self, qapp: Any) -> None:
        bridge = TerminalBridge(_FakePtyTransport)
        bridge.start()
        assert pump(qapp, lambda: bridge.is_connected)
        bridge.stop()
        bridge.stop()
        assert bridge.is_connected is False


# --------------------------------------------------------------------------------------
# TerminalCard graceful offline state (no Docker, no network)
# --------------------------------------------------------------------------------------


class _FakeCardController:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def kali_container(self) -> None:
        return None

    def readiness(self) -> Any:
        return SimpleNamespace(docker_running=False)

    async def execute_terminal_command(self, cmd: str) -> tuple[int, str, str]:
        self.commands.append(cmd)
        return 0, "FAKE_CONSOLE_OUTPUT_7Q", ""


@pytest.mark.skipif(not QT_AVAILABLE, reason="PySide6 not installed")
class TestTerminalCardFallback:
    def test_construction_is_offline_safe(self, qapp: Any) -> None:
        from bletiner.ui.terminal_card import TerminalCard

        card = TerminalCard()
        try:
            assert card.is_pty_connected is False
            assert card._bridge is None
            assert "Disconnected" in card.conn.text()
            card.append_system("notice")
            assert "notice" in card.view.toPlainText()
            card.clear()
            card.shutdown()
        finally:
            card.deleteLater()
            qapp.processEvents()

    def test_fallback_labels_and_runs_controller_commands(self, qapp: Any) -> None:
        from bletiner.ui.terminal_card import TerminalCard

        controller = _FakeCardController()
        card = TerminalCard(controller=controller, auto_connect=False)
        try:
            card.set_connected(False, "Docker Desktop is not running (test)")
            assert "Disconnected" in card.conn.text()
            assert "Docker Desktop is not running (test)" in card.conn.text()
            assert "Command console (PTY unavailable)" in card.status_detail.text()
            assert "Docker Desktop is not running (test)" in card.status_detail.text()
            assert not card.status_detail.isHidden()
            assert card.btn_reconnect.isHidden() is False

            entered: list[str] = []
            card.command_entered.connect(entered.append)
            card.cmd_input.setText("id")
            card._on_submit()
            assert entered == ["id"]
            assert pump(qapp, lambda: "FAKE_CONSOLE_OUTPUT_7Q" in card.view.toPlainText())
            assert controller.commands == ["id"]

            card.append_tool_output("nmap_fast", ["nmap", "10.0.0.5"], "22/tcp open ssh")
            card.append_dispatch("nmap -sT 10.0.0.5")
            text = card.view.toPlainText()
            assert "AGENT TOOL nmap_fast" in text
            assert "22/tcp open ssh" in text
            assert "AGENT DISPATCH: nmap -sT 10.0.0.5" in text
        finally:
            card.shutdown()
            card.deleteLater()
            qapp.processEvents()

    def test_key_encoding_from_qt_events(self, qapp: Any) -> None:
        from bletiner.ui.terminal_card import TerminalCard

        card = TerminalCard(auto_connect=False)
        try:

            def encode(
                key: int, modifiers: Any = Qt.KeyboardModifier.NoModifier, text: str = ""
            ) -> bytes | None:
                return card._encode_key_event(QKeyEvent(QEvent.Type.KeyPress, key, modifiers, text))

            assert encode(Qt.Key.Key_Return) == b"\r"
            assert encode(Qt.Key.Key_Up) == b"\x1b[A"
            assert encode(Qt.Key.Key_Down, Qt.KeyboardModifier.ControlModifier) == b"\x1b[1;5B"
            assert encode(Qt.Key.Key_Backtab) == b"\x1b[Z"
            assert encode(Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier, "\x03") == b"\x03"
            assert encode(Qt.Key.Key_X, Qt.KeyboardModifier.AltModifier, "x") == b"\x1bx"
            assert encode(Qt.Key.Key_Z, Qt.KeyboardModifier.ShiftModifier, "Z") == b"Z"
        finally:
            card.shutdown()
            card.deleteLater()
            qapp.processEvents()


class _ReadinessController:
    """Controller stub exposing the cached readiness snapshot the card is allowed to poll."""

    def __init__(
        self, *, ready: bool = True, probe_complete: bool = True, notes: tuple[str, ...] = ()
    ) -> None:
        self._ready = ready
        self._probe_complete = probe_complete
        self._notes = notes

    def set_snapshot(self, *, ready: bool, probe_complete: bool = True, notes: tuple[str, ...] = ()) -> None:
        self._ready = ready
        self._probe_complete = probe_complete
        self._notes = notes

    def readiness(self) -> Any:
        return SimpleNamespace(
            probe_complete=self._probe_complete,
            local_ready=self._ready,
            docker_running=self._ready,
            kali_container="my-kali" if self._ready else None,
            notes=self._notes,
        )

    def kali_container(self) -> None:
        return None

    async def execute_terminal_command(self, cmd: str) -> tuple[int, str, str]:
        return 0, "ok", ""


@pytest.mark.skipif(not QT_AVAILABLE, reason="PySide6 not installed")
class TestTerminalCardReadiness:
    def test_waits_then_attaches_when_controller_reports_ready(self, qapp: Any) -> None:
        from bletiner.ui.terminal_card import TerminalCard

        controller = _ReadinessController(ready=False, probe_complete=False)
        transport = _FakePtyTransport()
        card = TerminalCard(controller=controller, auto_connect=False)
        card._make_transport = lambda: transport
        try:
            card._wait_for_sandbox_readiness()
            assert card.is_pty_connected is False
            assert card._readiness_timer is not None and card._readiness_timer.isActive()
            assert "Waiting for the sandbox readiness check" in card.status_detail.text()

            controller.set_snapshot(ready=True)
            card._poll_controller_readiness()
            assert pump(qapp, lambda: card.is_pty_connected)
            assert card.conn.text() == "● Connected"
            assert card._readiness_timer is not None and not card._readiness_timer.isActive()

            # Connect-time setup: deterministic prompt + remote clear are really written to the PTY.
            received = bytearray()
            transport.peer.settimeout(0.1)
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                qapp.processEvents()
                with contextlib.suppress(TimeoutError):
                    received.extend(transport.peer.recv(4096))
                if b"\x0c" in received:
                    break
            assert b"export PS1=" in received
            assert b"\x0c" in received

            transport.peer.sendall(b"bletiner@kali:/workspace$ ready\r\n")
            assert pump(qapp, lambda: "ready" in card.view.toPlainText())
            assert "Connected to Kali sandbox container 'fake-kali'" in card.view.toPlainText()
            before = card.view.toPlainText()
            card.append_tool_output("nmap_fast", ["nmap", "10.0.0.5"], "22/tcp open ssh")
            assert not card.agent_frame.isHidden()
            assert card.view.toPlainText() == before
        finally:
            card.shutdown()
            card.deleteLater()
            qapp.processEvents()

    def test_not_ready_snapshot_enters_labelled_console(self, qapp: Any) -> None:
        from bletiner.ui.terminal_card import TerminalCard

        controller = _ReadinessController(ready=False, notes=("Docker Desktop is not running.",))
        card = TerminalCard(controller=controller, auto_connect=False)
        try:
            card._wait_for_sandbox_readiness()
            assert card.is_pty_connected is False
            assert (
                "Command console (PTY unavailable): Docker Desktop is not running."
                in card.status_detail.text()
            )
            assert "Command console mode" in card.view.toPlainText()
        finally:
            card.shutdown()
            card.deleteLater()
            qapp.processEvents()

    def test_probe_never_completes_falls_back_after_timeout(self, qapp: Any) -> None:
        from bletiner.ui.terminal_card import TerminalCard

        controller = _ReadinessController(ready=False, probe_complete=False)
        card = TerminalCard(controller=controller, auto_connect=False)
        try:
            card._wait_for_sandbox_readiness()
            assert card._readiness_timer is not None and card._readiness_timer.isActive()
            for _ in range(24):
                card._poll_controller_readiness()
            assert card.is_pty_connected is False
            assert "readiness check did not complete" in card.status_detail.text()
        finally:
            card.shutdown()
            card.deleteLater()
            qapp.processEvents()

    def test_pill_states_are_explicit(self, qapp: Any) -> None:
        from bletiner.ui.terminal_card import TerminalCard

        card = TerminalCard(auto_connect=False)
        try:
            card._set_pill("connecting")
            assert card.conn.text() == "● Connecting…"
            assert "245, 158, 11" in card.conn.styleSheet()
            card._set_pill("connected")
            assert card.conn.text() == "● Connected"
            assert "16, 185, 129" in card.conn.styleSheet()
            card._set_pill("error", "Docker Desktop is not running.")
            assert card.conn.text().startswith("● Disconnected — Docker Desktop is not running.")
            assert "239, 68, 68" in card.conn.styleSheet()
        finally:
            card.shutdown()
            card.deleteLater()
            qapp.processEvents()

    def test_maximize_signal_and_copy_do_not_start_docker(self, qapp: Any) -> None:
        from bletiner.ui.terminal_card import TerminalCard

        card = TerminalCard(auto_connect=False)
        fired: list[bool] = []
        card.maximize_toggled.connect(lambda: fired.append(True))
        try:
            card.btn_expand.click()
            assert fired == [True]
            assert card._bridge is None
            card._copy_all()
        finally:
            card.shutdown()
            card.deleteLater()
            qapp.processEvents()


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def test_no_ansi_control_codes_leak_into_plain_text() -> None:
    screen = AnsiScreen(40, 4)
    screen.feed(b"\x1b[1;32mOK\x1b[0m done")
    text = screen.render_text()
    assert "OK" in text and "done" in text
    assert not re.search(r"\x1b\[", text)
