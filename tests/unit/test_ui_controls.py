"""Offscreen UI tests for pause/stop controls and preflight-failure handling."""

from __future__ import annotations

import os
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from bletiner.agent.events import AgentState
from bletiner.ui import theme


def _qapp() -> Any:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    if app.styleSheet() != theme.QSS:
        theme.apply_dark_theme(app)
    return app


def test_pause_and_stop_are_enabled_for_the_whole_live_run() -> None:
    _qapp()
    from bletiner.ui.desktop import MainWindow

    win = MainWindow()
    try:
        win._apply_state(AgentState.INITIALIZING)
        assert win.bottom_bar.btn_pause.isEnabled()
        assert win.bottom_bar.btn_stop.isEnabled()
        assert win.bottom_bar.btn_pause.text() == "Pause"

        win._apply_state(AgentState.EXECUTING)
        assert win.bottom_bar.btn_pause.isEnabled()
        assert win.bottom_bar.btn_stop.isEnabled()

        win._apply_state(AgentState.PAUSED)
        assert win.bottom_bar.btn_pause.text() == "Resume"
        assert win.bottom_bar.btn_stop.isEnabled()  # Stop must stay available while paused

        win._apply_state(AgentState.STOPPED)
        assert not win.bottom_bar.btn_pause.isEnabled()
        assert not win.bottom_bar.btn_stop.isEnabled()
    finally:
        win.close()


def test_preflight_failure_returns_to_idle_without_fake_run() -> None:
    _qapp()
    from bletiner.ui.desktop import MainWindow

    win = MainWindow()
    try:
        win._apply_state(AgentState.INITIALIZING)
        win.investigation.set_progress(12, "Starting")
        win._handle_start_failure(
            ValueError("DeepSeek rejected the API key: Authentication failed (HTTP 401)")
        )
        assert win._state is AgentState.IDLE
        assert win._running is False
        assert win.investigation.progress.value() == 0
    finally:
        win.close()
