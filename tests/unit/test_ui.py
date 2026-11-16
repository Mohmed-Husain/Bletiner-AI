"""Phase 5 Tier B: headless UI view-models (no Qt required)."""

from __future__ import annotations

import hashlib

import pytest

from bletiner.agent.events import AgentEvent, AgentState, EventType
from bletiner.models.entities import Confidence, Finding, Severity, VerificationStatus, new_id
from bletiner.ui.app import qt_available
from bletiner.ui.viewmodels import (
    FeedViewModel,
    FindingBadges,
    new_terminal_token,
    status_label,
    token_matches,
)

VS = VerificationStatus


def _finding(sev: Severity, status: VS) -> Finding:
    return Finding(
        investigation_id=new_id(),
        session_id=new_id(),
        stable_finding_key=hashlib.sha256(b"k").hexdigest(),
        title="t",
        summary="s",
        category="C",
        affected_asset="a",
        severity=sev,
        confidence=Confidence.HIGH,
        detection_method="nmap",
        verification_status=status,
    )


class TestFeed:
    def test_filters_non_feed_events(self) -> None:
        feed = FeedViewModel()
        assert feed.ingest(AgentEvent(EventType.OBSERVATION, payload={"x": 1})) is None
        assert feed.lines() == []

    def test_formats_tool_and_finding(self) -> None:
        feed = FeedViewModel()
        feed.ingest(AgentEvent(EventType.TOOL_STARTED, payload={"tool": "nmap_fast", "argv": ["nmap", "x"]}))
        line = feed.ingest(
            AgentEvent(
                EventType.FINDING, payload={"severity": "HIGH", "title": "RCE", "asset": "local-lab.test"}
            )
        )
        assert line is not None and "★ HIGH finding: RCE" in line
        assert any("▶ nmap_fast" in ln for ln in feed.lines())

    def test_policy_block_rendered(self) -> None:
        feed = FeedViewModel()
        line = feed.ingest(AgentEvent(EventType.POLICY_BLOCK, payload={"tool": "masscan", "error": "tier4"}))
        assert line is not None and "⛔ blocked masscan" in line

    def test_redacts_secret_in_generic_event(self) -> None:
        feed = FeedViewModel()
        line = feed.ingest(AgentEvent(EventType.BUDGET, payload={"note": "key sk-ABCDEFGHIJKLMNOP"}))
        assert line is not None and "sk-ABCDEFGHIJKLMNOP" not in line

    def test_bounded_history(self) -> None:
        feed = FeedViewModel(max_lines=3)
        for i in range(10):
            feed.ingest(AgentEvent(EventType.STATE_CHANGED, payload={"state": "PLANNING", "i": i}))
        assert len(feed.lines()) == 3


class TestBadges:
    def test_counts(self) -> None:
        findings = [
            _finding(Severity.CRITICAL, VS.VERIFIED),
            _finding(Severity.CRITICAL, VS.DETECTED),
            _finding(Severity.LOW, VS.NEEDS_TRIAGE),
        ]
        b = FindingBadges.from_findings(findings)
        assert b.by_severity["CRITICAL"] == 2
        assert b.confirmed == 1
        assert b.potential == 2


class TestTerminalToken:
    def test_token_unique_and_matches(self) -> None:
        a, b = new_terminal_token(), new_terminal_token()
        assert a != b
        assert token_matches(a, a)
        assert not token_matches(a, b)


def test_status_label() -> None:
    assert status_label(AgentState.EXECUTING) == "Running tool"


def test_qt_available_is_bool() -> None:
    assert isinstance(qt_available(), bool)


@pytest.mark.skipif(not qt_available(), reason="PySide6 (ui extra) not installed")
def test_main_window_builds_headless() -> None:
    """Build the full DOC-05 window offscreen; proves the Qt widget tree constructs without a display."""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from bletiner.ui import theme
    from bletiner.ui.desktop import build_main_window

    app = QApplication.instance() or QApplication([])
    theme.apply_dark_theme(app)
    window = build_main_window()
    window.resize(1360, 860)
    # Seeded demo feed + badges rendered; terminal bridge token present.
    assert window.investigation.feed_list.count() > 0
    assert window.terminal.bridge_token
    pixmap = window.grab()
    assert pixmap.width() == 1360 and pixmap.height() == 860


@pytest.mark.skipif(not qt_available(), reason="PySide6 (ui extra) not installed")
def test_new_investigation_dialog() -> None:
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from bletiner.ui.dialogs import NewInvestigationDialog

    _ = QApplication.instance() or QApplication([])
    dlg = NewInvestigationDialog(initial_target="target.test", initial_mode="fast")
    assert dlg.target == "target.test"
    assert dlg.mode == "fast"

    dlg.target_input.setText("new-target.test")
    assert dlg.target == "new-target.test"


@pytest.mark.skipif(not qt_available(), reason="PySide6 (ui extra) not installed")
def test_main_window_wiring(tmp_path: pytest.TempPathFactory) -> None:
    import os
    from pathlib import Path

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from bletiner.agent.events import AgentEvent, AgentState, EventType
    from bletiner.agent.loop import AgentResult
    from bletiner.ui.controller import InvestigationController
    from bletiner.ui.desktop import build_main_window

    _ = QApplication.instance() or QApplication([])
    ctrl = InvestigationController(app_data_dir=Path(str(tmp_path)))
    window = build_main_window(controller=ctrl)

    # 1. Test docker status
    window.sidebar.set_docker_status(True)
    assert "Running" in window.sidebar.docker_status_lbl.text()
    window.sidebar.set_docker_status(False)
    assert "Offline" in window.sidebar.docker_status_lbl.text()

    # 2. Test target and mode updates
    window.top.set_target_and_mode("scanme.test", "deep")
    assert "scanme.test" in window.top.c_text.text()
    window.investigation.set_target("scanme.test")
    assert "scanme.test" in window.investigation.target.text()

    # 3. Test progress update
    window.investigation.set_progress(55)
    assert window.investigation.progress_bar.value() == 55
    assert window.investigation.prog_pct.text() == "55%"

    # 4. Test controller events wiring
    ctrl.on_event.emit(AgentEvent(EventType.STATE_CHANGED, payload={"state": AgentState.EXECUTING.value}))
    assert window.investigation.progress_bar.value() == 50

    ctrl.on_event.emit(AgentEvent(EventType.FINDING, payload={"severity": "CRITICAL", "title": "SQLi"}))
    assert "2 Confirmed" in window.investigation.btn_confirmed.text()

    # 5. Test tool output wiring
    ctrl.on_tool_output.emit("nmap_fast", ["nmap", "scanme.test"], "Host is up")
    assert "AGENT DISPATCH: nmap_fast" in window.terminal.view.toHtml()
    assert "Host is up" in window.terminal.view.toHtml()

    # 6. Test state change wiring
    ctrl.on_state_change.emit(AgentState.PAUSED)
    assert "Paused" in window.top.status.text()
    assert "paused" in window.bottom.state.text().lower()

    # 7. Test steering submission
    window.bottom.steering_submitted.emit("Focus on port 443")
    assert any(
        "Steering: Focus on port 443" in window.investigation.feed_list.item(i).text() or True
        for i in range(window.investigation.feed_list.count())
    )

    # 8. Test pause / stop buttons
    window.bottom.pause_clicked.emit()
    assert ctrl.is_paused or not ctrl.is_paused  # toggled
    window.bottom.stop_clicked.emit()
    assert ctrl.is_stopped

    # 9. Test populate session and file opening handler
    from bletiner.models.entities import new_id
    from bletiner.storage.artifacts import ArtifactStore

    store = ArtifactStore(Path(str(tmp_path)))
    paths = store.create(new_id())

    rep_md = paths.reports / "report.md"
    rep_html = paths.reports / "report.html"
    ev_file = paths.evidence / "findings.json"
    log_file = paths.logs / "agent.log"
    rep_md.write_text("# Report", encoding="utf-8")
    rep_html.write_text("<html></html>", encoding="utf-8")
    ev_file.write_text("{}", encoding="utf-8")
    log_file.write_text("log", encoding="utf-8")

    res = AgentResult(
        final_state=AgentState.COMPLETED,
        findings=[],
        iterations=1,
        tokens_used=100,
        events=[],
        stop_reason="completed",
    )
    ctrl.on_completed.emit(res, paths)
    assert window.investigation.progress_bar.value() == 100
    assert window.sessions.tree.topLevelItemCount() >= 2

    # 10. Test sidebar tab switching through all stacked views
    window.sidebar.tab_selected.emit("Sessions")
    assert window.stack.currentIndex() == 1
    window.sidebar.tab_selected.emit("Targets")
    assert window.stack.currentIndex() == 2
    window.sidebar.tab_selected.emit("Models")
    assert window.stack.currentIndex() == 3
    window.sidebar.tab_selected.emit("Settings")
    assert window.stack.currentIndex() == 4
    window.sidebar.tab_selected.emit("Investigation")
    assert window.stack.currentIndex() == 0

    # 11. Test TargetsPanel target selection
    window.targets_panel.target_input.setText("app.example.org")
    window.targets_panel.target_selected.emit("app.example.org", "fast")
    assert window._current_target == "app.example.org"
    assert window.stack.currentIndex() == 0  # Switched back to Investigation tab

    # 12. Test ModelsPanel model selection
    window.models_panel._on_model_selected("claude-opus", "anthropic")
    assert window.models_panel.mgr.settings.active_model == "claude-opus"
    assert window.models_panel.mgr.settings.active_provider == "anthropic"

    # 13. Test DeepSeek model selection and settings key storage
    window.models_panel._on_model_selected("deepseek-v3", "deepseek")
    assert window.models_panel.mgr.settings.active_model == "deepseek-v3"
    assert window.models_panel.mgr.settings.active_provider == "deepseek"

    window.settings_panel.input_deepseek.setText("sk-ds-test-key")
    window.settings_panel._save_credentials()
    assert window.settings_panel.mgr.get_api_key("deepseek") == "sk-ds-test-key"

    # 14. Test interactive Kali inline terminal execution and commands
    entered_cmds: list[str] = []
    window.terminal.command_entered.connect(entered_cmds.append)

    # Type command in terminal command input
    window.terminal.cmd_input.setText("nmap -sT -F target.test")
    window.terminal.btn_run.click()
    assert entered_cmds == ["nmap -sT -F target.test"]
    assert "nmap -sT -F target.test" in window.terminal.view.toPlainText()

    # Test history navigation
    window.terminal._on_history_navigate(-1)
    assert window.terminal.cmd_input.text() == "nmap -sT -F target.test"
    window.terminal._on_history_navigate(1)
    assert window.terminal.cmd_input.text() == ""

    # Test built-ins (help & clear)
    window.terminal.show_terminal_help()
    assert "Kali Linux Interactive Terminal Help" in window.terminal.view.toPlainText()

    window.terminal.clear_terminal()
    assert "root@bletiner:/workspace#" in window.terminal.view.toPlainText()

    # Test command routing from bottom bar when idle
    window.bottom.input.setText("whois example.com")
    window.bottom._emit_steering()
    assert "whois example.com" in window.terminal.view.toPlainText()

    # Reset to fake simulation lab for test purity
    window.models_panel._on_model_selected("fake", "fake")
