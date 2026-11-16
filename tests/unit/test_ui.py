"""Headless UI tests: view-models plus the real desktop shell under QT_QPA_PLATFORM=offscreen.

The desktop tests build the full MainWindow with a fake controller that follows the InvestigationController
INTERFACES contract (Readiness / start(**kwargs) / sessions() / current_findings / signal bridges). They assert
the UI reflects real controller state only: no demo strings, no fabricated progress, real gating and real
error surfacing. Offscreen construction must never touch Docker or open a network socket.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from bletiner.agent.events import AgentEvent, AgentState, EventType
from bletiner.models.entities import Confidence, Finding, Severity, VerificationStatus, new_id
from bletiner.services.session_store import SessionSummary
from bletiner.ui import theme, viewmodels
from bletiner.ui.app import qt_available
from bletiner.ui.viewmodels import (
    FeedViewModel,
    FindingBadges,
    feed_row,
    format_duration,
    format_relative_time,
    new_terminal_token,
    progress_for_state,
    start_block_reason,
    status_label,
    token_matches,
)

VS = VerificationStatus


# ---------------------------------------------------------------------------------------------------------
# Fixtures / fakes
# ---------------------------------------------------------------------------------------------------------
def _qapp() -> Any:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    if app.styleSheet() != theme.QSS:
        theme.apply_dark_theme(app)
    return app


@dataclass(frozen=True)
class FakeReadiness:
    docker_running: bool = True
    gate_certified: bool = True
    kali_container: str | None = "my-kali"
    secret_backend: str = "Windows DPAPI"
    ai_ready: bool = False
    provider: str = ""
    model: str = ""
    ollama_running: bool = False
    notes: tuple[str, ...] = ()
    probe_complete: bool = True

    @property
    def local_ready(self) -> bool:
        return self.docker_running and self.kali_container is not None

    @property
    def ai_engine_ready(self) -> bool:
        return self.ai_ready and self.gate_certified and self.local_ready


class Bridge:
    def __init__(self) -> None:
        self._slots: list[Any] = []

    def connect(self, slot: Any) -> None:
        self._slots.append(slot)

    def emit(self, *args: Any) -> None:
        for slot in list(self._slots):
            slot(*args)


def _finding(severity: Severity, status: VS = VS.VERIFIED) -> Finding:
    return Finding(
        investigation_id=new_id(),
        session_id=new_id(),
        stable_finding_key=hashlib.sha256(b"k").hexdigest(),
        title="t",
        summary="s",
        category="C",
        affected_asset="a",
        severity=severity,
        confidence=Confidence.HIGH,
        detection_method="nmap",
        verification_status=status,
    )


def _summary(target: str = "10.0.0.5", status: str = "completed") -> SessionSummary:
    started = datetime.now(UTC) - timedelta(minutes=5)
    return SessionSummary(
        investigation_id=new_id(),
        session_id=new_id(),
        target=target,
        mode="deep",
        engine="local",
        provider="",
        model="",
        status=status,
        findings_total=2,
        findings_confirmed=1,
        started_at=started,
        completed_at=started + timedelta(seconds=30),
        duration_s=30.0,
        tokens_used=0,
        root=SimpleNamespace(),  # type: ignore[arg-type]
        report_md=None,
        report_html=None,
        findings_json=None,
    )


class FakeController:
    """Minimal InvestigationController stand-in honouring the INTERFACES contract."""

    def __init__(self, readiness: FakeReadiness | None = None) -> None:
        self._readiness = readiness or FakeReadiness()
        self.on_event = Bridge()
        self.on_tool_output = Bridge()
        self.on_state_change = Bridge()
        self.on_completed = Bridge()
        self.on_error = Bridge()
        self.current_state = AgentState.IDLE
        self.is_running = False
        self.is_paused = False
        self.is_stopped = False
        self.current_findings: list[Finding] = []
        self.current_paths: Any = None
        self.calls: list[dict[str, Any]] = []
        self.pause_calls = 0
        self.resume_calls = 0
        self.stop_calls = 0
        self.steering_calls: list[str] = []
        self.sessions_result: list[SessionSummary] = []
        self.findings_result: list[Finding] = []
        self.outcome: Any = None
        self.auto_emit: bool = True

    # -- readiness / validation -------------------------------------------------------------------
    def readiness(self) -> FakeReadiness:
        return self._readiness

    def set_readiness(self, **changes: Any) -> None:
        data = {
            "docker_running": self._readiness.docker_running,
            "gate_certified": self._readiness.gate_certified,
            "kali_container": self._readiness.kali_container,
            "secret_backend": self._readiness.secret_backend,
            "ai_ready": self._readiness.ai_ready,
            "provider": self._readiness.provider,
            "model": self._readiness.model,
            "ollama_running": self._readiness.ollama_running,
            "notes": self._readiness.notes,
        }
        data.update(changes)
        self._readiness = FakeReadiness(**data)

    def validate_target(self, raw: str) -> tuple[str | None, str | None]:
        cleaned = raw.strip()
        if "://" in cleaned:
            cleaned = cleaned.split("://", 1)[1]
        cleaned = cleaned.split("/", 1)[0]
        if not cleaned:
            return None, "Enter a target domain or IPv4 address."
        return cleaned, None

    def target_in_authorized_scope(self, cleaned: str, authorized: list[str]) -> bool:
        return True

    def kali_container(self) -> Any:
        return SimpleNamespace(name="my-kali", status="running")

    # -- lifecycle -------------------------------------------------------------------------------
    def start(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        self.is_running = True
        self.current_paths = SimpleNamespace(
            root=SimpleNamespace(),
            reports=SimpleNamespace(),
            evidence=SimpleNamespace(),
            logs=SimpleNamespace(),
        )

        async def _run() -> Any:
            await asyncio.sleep(0)
            if self.auto_emit:
                self.on_event.emit(
                    AgentEvent(
                        EventType.TOOL_STARTED,
                        payload={"tool": "nmap_fast", "argv": ["nmap", "-sT", kwargs["target"]]},
                    )
                )
                self.on_state_change.emit(AgentState.EXECUTING)
                self.is_running = False
                self.on_state_change.emit(AgentState.COMPLETED)
                if self.outcome is not None:
                    self.on_completed.emit(self.outcome)
                return self.outcome
            return None

        return _run()

    def pause(self) -> bool:
        self.pause_calls += 1
        self.is_paused = True
        self.on_state_change.emit(AgentState.PAUSED)
        return True

    def resume(self) -> None:
        self.resume_calls += 1
        self.is_paused = False
        self.on_state_change.emit(AgentState.EXECUTING)

    def stop(self) -> None:
        self.stop_calls += 1
        self.is_stopped = True
        self.is_running = False
        self.on_state_change.emit(AgentState.STOPPED)

    def submit_steering(self, instruction: str) -> Any:
        self.steering_calls.append(instruction)
        return SimpleNamespace(accepted=True, reason="")

    async def execute_terminal_command(self, cmd: str) -> tuple[int, str, str]:
        return 0, "", ""

    async def sessions(self, limit: int = 100) -> list[SessionSummary]:
        return list(self.sessions_result)

    async def findings_for(self, investigation_id: str) -> list[Finding]:
        return list(self.findings_result)

    async def delete_session(self, investigation_id: str) -> None:
        return None

    def shutdown(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _hermetic_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests independent of the operator's real ~/.bletiner settings."""
    from bletiner.ui import desktop

    monkeypatch.setattr(desktop, "_settings_prefill", lambda: ("", "deep"))
    monkeypatch.setattr(desktop, "_settings_authorized_scope", lambda: None)


@pytest.fixture
def app() -> Any:
    return _qapp()


@pytest.fixture
def window_factory(app: Any) -> Any:
    from bletiner.ui.desktop import build_main_window

    created: list[Any] = []

    def factory(controller: Any = None) -> Any:
        window = build_main_window(controller=controller)
        created.append(window)
        return window

    yield factory
    from PySide6.QtCore import QCoreApplication, QEvent

    for window in created:
        terminal = getattr(window, "terminal", None)
        shutdown = getattr(terminal, "shutdown", None)
        if callable(shutdown):
            with contextlib.suppress(Exception):
                shutdown()
        window.close()
        window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def _all_texts(widget: Any) -> list[str]:
    from PySide6.QtWidgets import QComboBox, QLabel, QLineEdit, QListWidget, QPushButton

    texts: list[str] = []
    for label in widget.findChildren(QLabel):
        texts.append(label.text())
    for button in widget.findChildren(QPushButton):
        texts.append(button.text())
        texts.append(button.toolTip())
    for line_edit in widget.findChildren(QLineEdit):
        texts.append(line_edit.text())
        texts.append(line_edit.placeholderText())
    for combo in widget.findChildren(QComboBox):
        texts.extend(combo.itemText(i) for i in range(combo.count()))
        texts.append(combo.currentText())
    for listing in widget.findChildren(QListWidget):
        texts.extend(listing.item(i).text() for i in range(listing.count()))
    return [text for text in texts if text]


# ---------------------------------------------------------------------------------------------------------
# View-model tests
# ---------------------------------------------------------------------------------------------------------
class TestFeed:
    def test_formats_tool_and_finding(self) -> None:
        feed = FeedViewModel()
        feed.ingest(AgentEvent(EventType.TOOL_STARTED, payload={"tool": "nmap_fast", "argv": ["nmap", "x"]}))
        line = feed.ingest(
            AgentEvent(
                EventType.FINDING, payload={"severity": "HIGH", "title": "RCE", "asset": "local-lab.test"}
            )
        )
        assert line is not None and "HIGH finding: RCE" in line
        assert any("nmap_fast" in text for text in feed.lines())

    def test_observation_is_real_feed_content(self) -> None:
        row = feed_row(AgentEvent(EventType.OBSERVATION, payload={"message": "nuclei skipped"}))
        assert row is not None
        assert row.text == "nuclei skipped"
        assert row.kind == "info"

    def test_policy_block_rendered(self) -> None:
        feed = FeedViewModel()
        line = feed.ingest(AgentEvent(EventType.POLICY_BLOCK, payload={"tool": "masscan", "error": "tier4"}))
        assert line is not None and "Blocked masscan" in line

    def test_error_and_steering_rows(self) -> None:
        error_row = feed_row(AgentEvent(EventType.ERROR, payload={"tool": "nmap", "error": "boom"}))
        assert error_row is not None and error_row.kind == "error"
        steering_row = feed_row(AgentEvent(EventType.STEERING_REJECTED, payload={"reason": "local engine"}))
        assert steering_row is not None and "local engine" in steering_row.text

    def test_redacts_secret_in_generic_event(self) -> None:
        feed = FeedViewModel()
        line = feed.ingest(AgentEvent(EventType.BUDGET, payload={"note": "key sk-ABCDEFGHIJKLMNOP"}))
        assert line is not None and "sk-ABCDEFGHIJKLMNOP" not in line

    def test_bounded_history(self) -> None:
        feed = FeedViewModel(max_lines=3)
        for _ in range(10):
            feed.ingest(AgentEvent(EventType.STATE_CHANGED, payload={"state": "PLANNING"}))
        assert len(feed.lines()) == 3


class TestBadges:
    def test_counts(self) -> None:
        findings = [
            _finding(Severity.CRITICAL, VS.VERIFIED),
            _finding(Severity.CRITICAL, VS.DETECTED),
            _finding(Severity.LOW, VS.NEEDS_TRIAGE),
        ]
        badges = FindingBadges.from_findings(findings)
        assert badges.by_severity["CRITICAL"] == 2
        assert badges.confirmed == 1
        assert badges.potential == 2

    def test_tooltip_lists_only_real_counts(self) -> None:
        tooltip = viewmodels.badge_tooltip(FindingBadges.from_findings([_finding(Severity.HIGH)]))
        assert "1 HIGH" in tooltip and "0 INFO" not in tooltip


class TestProgress:
    def test_phase_progress_never_moves_backwards(self) -> None:
        assert progress_for_state(AgentState.INITIALIZING) == 5
        executing = progress_for_state(AgentState.EXECUTING)
        assert progress_for_state(AgentState.PLANNING, executing) == executing
        assert progress_for_state(AgentState.COMPLETED, executing) == 100

    def test_stopped_does_not_fake_completion(self) -> None:
        assert progress_for_state(AgentState.STOPPED, 55) == 55
        assert progress_for_state(AgentState.ERROR_HANDLING, 30) == 30

    def test_phase_description_counts(self) -> None:
        text = viewmodels.phase_description(AgentState.EXECUTING, tools=4, findings=2)
        assert "Running tool" in text and "4 tool runs" in text and "2 findings" in text


class TestReadinessRules:
    def test_controller_not_attached_blocks_start(self) -> None:
        reason = start_block_reason(
            available=False,
            docker_running=False,
            gate_certified=False,
            kali_container=None,
            ai_ready=False,
            engine="local",
        )
        assert reason is not None and "controller" in reason.lower()

    def test_local_engine_needs_only_docker_and_kali(self) -> None:
        ready = start_block_reason(
            available=True,
            docker_running=True,
            gate_certified=True,
            kali_container="my-kali",
            ai_ready=False,
            engine="local",
        )
        assert ready is None
        offline = start_block_reason(
            available=True,
            docker_running=False,
            gate_certified=True,
            kali_container=None,
            ai_ready=False,
            engine="local",
        )
        assert offline is not None and "Docker" in offline
        # Gate 3 is only required for the autonomous AI path, not deterministic local recon.
        no_gate = start_block_reason(
            available=True,
            docker_running=True,
            gate_certified=False,
            kali_container="my-kali",
            ai_ready=False,
            engine="local",
        )
        assert no_gate is None

    def test_ai_engine_needs_key_and_certified_gate(self) -> None:
        reason = start_block_reason(
            available=True,
            docker_running=True,
            gate_certified=True,
            kali_container="my-kali",
            ai_ready=False,
            engine="ai",
        )
        assert reason is not None and "API key" in reason
        gate_reason = start_block_reason(
            available=True,
            docker_running=True,
            gate_certified=False,
            kali_container="my-kali",
            ai_ready=True,
            engine="ai",
        )
        assert gate_reason is not None and "gate" in gate_reason.lower()

    def test_ai_toggle_block_reason_is_specific(self) -> None:
        assert "key" in (
            viewmodels.ai_engine_block_reason(
                docker_running=True,
                gate_certified=True,
                kali_container="my-kali",
                ai_ready=False,
            )
            or ""
        )
        assert "gate" in (
            viewmodels.ai_engine_block_reason(
                docker_running=True,
                gate_certified=False,
                kali_container="my-kali",
                ai_ready=True,
            )
            or ""
        )
        assert (
            viewmodels.ai_engine_block_reason(
                docker_running=True,
                gate_certified=True,
                kali_container="my-kali",
                ai_ready=True,
            )
            is None
        )

    def test_checking_is_an_info_issue(self) -> None:
        issues = viewmodels.readiness_issues(
            available=True,
            docker_running=False,
            gate_certified=False,
            kali_container=None,
            ai_ready=False,
            engine="local",
            checking=True,
        )
        assert len(issues) == 1 and issues[0].severity == "info"

    def test_issues_reflect_real_failures(self) -> None:
        issues = viewmodels.readiness_issues(
            available=True,
            docker_running=False,
            gate_certified=False,
            kali_container=None,
            ai_ready=False,
            engine="ai",
            provider="deepseek",
        )
        keys = {issue.key for issue in issues}
        assert "docker" in keys and "gate" in keys and "ai_key" in keys


class TestFormatting:
    def test_duration_and_model_labels(self) -> None:
        assert format_duration(12.34) == "12.3s"
        assert format_duration(None) == "\u2014"
        assert viewmodels.model_label("", "") == "No AI model configured"
        assert viewmodels.engine_label("ai") == "AI agent"
        assert viewmodels.mode_label("deep") == "Deep investigation"

    def test_relative_time_handles_none_and_recent(self) -> None:
        assert format_relative_time(None) == "unknown time"
        assert format_relative_time(datetime.now(UTC)) == "just now"


class TestTerminalToken:
    def test_token_unique_and_matches(self) -> None:
        first, second = new_terminal_token(), new_terminal_token()
        assert first != second
        assert token_matches(first, first)
        assert not token_matches(first, second)


def test_status_label() -> None:
    assert status_label(AgentState.EXECUTING) == "Running tool"


def test_qt_available_is_bool() -> None:
    assert isinstance(qt_available(), bool)


# ---------------------------------------------------------------------------------------------------------
# Theme / window integrity
# ---------------------------------------------------------------------------------------------------------
@pytest.mark.skipif(not qt_available(), reason="PySide6 not installed")
def test_theme_transparent_labels_and_dark_titlebar_safe(app: Any) -> None:
    assert "QLabel {" in theme.QSS
    assert "background: transparent" in theme.QSS.split("QLabel {", 1)[1].split("}", 1)[0]
    assert theme.apply_dark_titlebar(SimpleNamespace(winId=lambda: 0)) is False


@pytest.mark.skipif(not qt_available(), reason="PySide6 not installed")
def test_main_window_builds_headless_without_controller(window_factory: Any) -> None:
    window = window_factory(None)
    window.resize(1360, 860)
    assert window.stack.count() == 5
    assert window.investigation.btn_start.isEnabled() is False
    assert window.investigation.banner.isHidden() is False
    assert window.investigation.feed_list.count() == 1  # honest empty-state placeholder
    assert window.sidebar.buttons["Investigation"].is_active()
    assert window.terminal is not None and window.sessions is not None
    pixmap = window.grab()
    assert pixmap.width() == 1360 and pixmap.height() == 860


@pytest.mark.skipif(not qt_available(), reason="PySide6 not installed")
def test_no_demo_data_in_window(window_factory: Any) -> None:
    window = window_factory(None)
    forbidden = (
        "example.com",
        "2 potential",
        "1 confirmed",
        "42%",
        "deterministic simulation",
        "simulation lab",
        "session 001",
        "fakeprovider",
    )
    haystack = " ".join(_all_texts(window)).lower()
    for needle in forbidden:
        assert needle not in haystack, f"demo string {needle!r} leaked into the UI"
    assert window.investigation.percent_label.text() == "0%"
    assert window.investigation.progress.value() == 0


@pytest.mark.skipif(not qt_available(), reason="PySide6 not installed")
def test_top_bar_capsule_and_subtitle(window_factory: Any) -> None:
    window = window_factory(None)
    assert "Target:" in window.top_bar.capsule_text.text()
    assert "not set" in window.top_bar.capsule_text.text()
    assert window.top_bar.subtitle.text() == "Autonomous Security Investigation Workspace"
    window.top_bar.set_target_mode("10.0.0.5", "deep")
    assert "10.0.0.5" in window.top_bar.capsule_text.text()
    assert "Deep" in window.top_bar.capsule_text.text()


@pytest.mark.skipif(not qt_available(), reason="PySide6 not installed")
def test_no_fake_window_buttons(window_factory: Any) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QPushButton

    window = window_factory(None)
    names = {button.objectName() for button in window.findChildren(QPushButton)}
    assert not names & {"winMin", "winMax", "winClose"}
    assert not window.windowFlags() & Qt.WindowType.FramelessWindowHint


# ---------------------------------------------------------------------------------------------------------
# Readiness gating
# ---------------------------------------------------------------------------------------------------------
@pytest.mark.skipif(not qt_available(), reason="PySide6 not installed")
def test_docker_offline_disables_start_and_shows_banner(window_factory: Any) -> None:
    controller = FakeController(
        FakeReadiness(docker_running=False, gate_certified=False, kali_container=None)
    )
    window = window_factory(controller)
    assert window.investigation.btn_start.isEnabled() is False
    assert window.investigation.banner.isHidden() is False
    assert "Docker Desktop is offline" in window.investigation.banner.text.text()
    assert "Offline" in window.sidebar.docker_status.text()
    assert window.investigation.btn_ai.isEnabled() is False


@pytest.mark.skipif(not qt_available(), reason="PySide6 not installed")
def test_local_ready_enables_start_but_ai_needs_key(window_factory: Any) -> None:
    controller = FakeController()
    window = window_factory(controller)
    assert window.investigation.banner.isHidden() is True
    assert window.investigation.btn_start.isEnabled() is True
    assert window.investigation.btn_ai.isEnabled() is False
    controller.set_readiness(ai_ready=True, provider="deepseek", model="deepseek-chat")
    window.refresh_readiness()
    assert window.investigation.btn_ai.isEnabled() is True
    window.investigation.set_engine("ai")
    assert window.investigation.engine() == "ai"
    assert window.investigation.btn_start.isEnabled() is True
    assert window.footer.engine_label.text() == "Engine: AI agent"
    assert "deepseek-chat" in window.footer.model_label.text()


@pytest.mark.skipif(not qt_available(), reason="PySide6 not installed")
def test_unavailable_controller_shows_honest_banner(window_factory: Any) -> None:
    window = window_factory(None)
    assert window.investigation.banner.isHidden() is False
    assert "controller" in window.investigation.banner.text.text().lower()
    assert window.investigation.btn_start.isEnabled() is False


# ---------------------------------------------------------------------------------------------------------
# Start / event / completion wiring
# ---------------------------------------------------------------------------------------------------------
@pytest.mark.skipif(not qt_available(), reason="PySide6 not installed")
def test_start_calls_controller_with_contract_kwargs(window_factory: Any) -> None:
    controller = FakeController()
    controller.outcome = SimpleNamespace(
        target="10.0.0.5",
        engine="local",
        status="completed",
        findings=[],
        paths=SimpleNamespace(
            root=SimpleNamespace(),
            reports=SimpleNamespace(),
            evidence=SimpleNamespace(),
            logs=SimpleNamespace(),
        ),
        reports=None,
        stop_reason="completed",
        duration_s=1.2,
        tokens_used=0,
        iterations=2,
    )
    window = window_factory(controller)
    window.investigation.set_target(" 10.0.0.5 ")
    window.investigation.set_mode("fast")

    async def scenario() -> None:
        window._on_start_requested()
        await asyncio.sleep(0.05)

    asyncio.run(scenario())
    assert controller.calls, "controller.start was not called"
    call = controller.calls[-1]
    assert call["target"] == "10.0.0.5"
    assert call["mode"] == "fast"
    assert call["engine"] == "local"
    assert call["api_key"] is None
    assert window.investigation.progress.value() == 100
    assert window._state is AgentState.COMPLETED


@pytest.mark.skipif(not qt_available(), reason="PySide6 not installed")
def test_start_without_event_loop_surfaces_error(window_factory: Any) -> None:
    controller = FakeController()
    window = window_factory(controller)
    window.investigation.set_target("10.0.0.5")
    window._on_start_requested()
    assert window.investigation.error_banner.isHidden() is False
    assert "event loop" in window.investigation.error_text.text().lower()
    assert window.investigation.btn_start.isEnabled() is True


@pytest.mark.skipif(not qt_available(), reason="PySide6 not installed")
def test_invalid_target_surfaces_inline_error(window_factory: Any) -> None:
    controller = FakeController()
    window = window_factory(controller)

    async def scenario() -> None:
        window.investigation.set_target("")
        window._on_start_requested()
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert window.investigation.error_banner.isHidden() is False
    assert "target" in window.investigation.error_text.text().lower()
    assert controller.calls == []


@pytest.mark.skipif(not qt_available(), reason="PySide6 not installed")
def test_controller_events_update_progress_feed_and_badges(window_factory: Any) -> None:
    controller = FakeController()
    window = window_factory(controller)
    controller.on_event.emit(AgentEvent(EventType.STATE_CHANGED, payload={"state": "EXECUTING"}))
    assert "Running tool" in window.top_bar.status_pill.text()
    assert window.investigation.progress.value() >= 30
    controller.on_event.emit(
        AgentEvent(EventType.TOOL_COMPLETED, payload={"tool": "nmap_fast", "exit_code": 0})
    )
    assert window.investigation.feed_list.count() >= 1
    controller.current_findings = [
        _finding(Severity.HIGH, VS.VERIFIED),
        _finding(Severity.MEDIUM, VS.DETECTED),
    ]
    controller.on_event.emit(AgentEvent(EventType.FINDING, payload={"severity": "HIGH", "title": "RCE"}))
    assert window.investigation.btn_confirmed.count.text() == "1"
    assert window.investigation.btn_potential.count.text() == "1"


@pytest.mark.skipif(not qt_available(), reason="PySide6 not installed")
def test_tool_output_routes_to_terminal_public_api(window_factory: Any) -> None:
    controller = FakeController()
    window = window_factory(controller)
    captured: list[tuple[Any, ...]] = []
    system: list[str] = []

    def _capture(*args: Any) -> None:
        captured.append(args)

    window.terminal.append_tool_output = _capture  # type: ignore[method-assign]
    window.terminal.append_system = system.append  # type: ignore[method-assign]
    controller.on_tool_output.emit("nmap_fast", ["nmap", "-sT"], "Host is up")
    controller.on_tool_output.emit("[SYSTEM]", [], "local recon engine starting")
    assert captured and captured[0][0] == "nmap_fast"
    assert system and "local recon engine starting" in system[0]


@pytest.mark.skipif(not qt_available(), reason="PySide6 not installed")
def test_completion_updates_ui_and_sessions_card(window_factory: Any) -> None:
    controller = FakeController()
    window = window_factory(controller)
    recorded: list[tuple[Any, ...]] = []
    window.sessions.set_current = lambda *args, **kwargs: recorded.append((args, kwargs))  # type: ignore[method-assign]
    paths = SimpleNamespace(
        root=SimpleNamespace(), reports=SimpleNamespace(), evidence=SimpleNamespace(), logs=SimpleNamespace()
    )
    outcome = SimpleNamespace(
        target="10.0.0.5",
        engine="local",
        status="completed",
        findings=[_finding(Severity.HIGH, VS.VERIFIED), _finding(Severity.LOW, VS.DETECTED)],
        paths=paths,
        reports=None,
        stop_reason="completed",
        duration_s=4.2,
        tokens_used=0,
        iterations=3,
    )
    controller.on_completed.emit(outcome)
    assert window.investigation.progress.value() == 100
    assert window.investigation.btn_confirmed.count.text() == "1"
    assert recorded, "sessions card was not given the completed investigation paths"
    assert recorded[0][0][0] == "10.0.0.5"
    assert recorded[0][1]["findings_total"] == 2
    assert window._state is AgentState.COMPLETED


@pytest.mark.skipif(not qt_available(), reason="PySide6 not installed")
def test_error_outcome_keeps_the_failure_visible(window_factory: Any) -> None:
    controller = FakeController()
    window = window_factory(controller)
    outcome = SimpleNamespace(
        target="10.0.0.5",
        engine="local",
        status="error",
        findings=[],
        paths=None,
        reports=None,
        stop_reason="error",
        duration_s=0.5,
        tokens_used=0,
        iterations=0,
        notes=("Docker container died mid-run",),
    )
    controller.on_completed.emit(outcome)
    assert window.investigation.error_banner.isHidden() is False
    assert "Docker container died mid-run" in window.investigation.error_text.text()
    assert window.investigation.progress.value() < 100


@pytest.mark.skipif(not qt_available(), reason="PySide6 not installed")
def test_controller_error_is_visible_everywhere(window_factory: Any) -> None:
    controller = FakeController()
    window = window_factory(controller)
    system: list[str] = []
    window.terminal.append_system = system.append  # type: ignore[method-assign]
    controller.on_error.emit(ValueError("Kali sandbox is not available"))
    assert window.investigation.error_banner.isHidden() is False
    assert "Kali sandbox is not available" in window.investigation.error_text.text()
    assert any("Kali sandbox is not available" in line for line in system)


# ---------------------------------------------------------------------------------------------------------
# Operator controls
# ---------------------------------------------------------------------------------------------------------
@pytest.mark.skipif(not qt_available(), reason="PySide6 not installed")
def test_pause_resume_and_stop(window_factory: Any) -> None:
    controller = FakeController()
    window = window_factory(controller)
    window._apply_state(AgentState.EXECUTING)
    window.bottom_bar.pause_clicked.emit()
    assert controller.pause_calls == 1
    assert window.bottom_bar.btn_pause.text() == "Resume"
    window.bottom_bar.pause_clicked.emit()
    assert controller.resume_calls == 1
    assert window.bottom_bar.btn_pause.text() == "Pause"

    monkeypatch_confirm = lambda: True  # noqa: E731
    window._confirm_stop = monkeypatch_confirm  # type: ignore[method-assign]
    window.bottom_bar.stop_clicked.emit()
    assert controller.stop_calls == 1


@pytest.mark.skipif(not qt_available(), reason="PySide6 not installed")
def test_stop_asks_for_confirmation(window_factory: Any) -> None:
    controller = FakeController()
    window = window_factory(controller)
    window._apply_state(AgentState.EXECUTING)
    window._confirm_stop = lambda: False  # type: ignore[method-assign]
    window.bottom_bar.stop_clicked.emit()
    assert controller.stop_calls == 0


@pytest.mark.skipif(not qt_available(), reason="PySide6 not installed")
def test_steering_only_available_for_ai_engine(window_factory: Any) -> None:
    controller = FakeController(FakeReadiness(ai_ready=True, provider="deepseek", model="deepseek-chat"))
    window = window_factory(controller)
    window.investigation.set_engine("ai")
    window._apply_state(AgentState.EXECUTING)
    assert window.bottom_bar.steering_input.isEnabled() is True
    window.bottom_bar.steering_input.setText("check port 443")
    window.bottom_bar.steering_submitted.emit("check port 443")
    assert controller.steering_calls == ["check port 443"]
    assert window.bottom_bar.steering_input.text() == ""


@pytest.mark.skipif(not qt_available(), reason="PySide6 not installed")
def test_local_engine_steering_is_disabled_and_explained(window_factory: Any) -> None:
    controller = FakeController()
    window = window_factory(controller)
    window.investigation.set_engine("local")
    window._apply_state(AgentState.EXECUTING)
    assert window.bottom_bar.steering_input.isEnabled() is False
    assert "does not accept steering" in window.bottom_bar.steering_input.placeholderText()


# ---------------------------------------------------------------------------------------------------------
# Navigation, shortcuts, panels, sessions
# ---------------------------------------------------------------------------------------------------------
@pytest.mark.skipif(not qt_available(), reason="PySide6 not installed")
def test_tab_switching_and_active_state(window_factory: Any) -> None:
    window = window_factory(None)
    for page in ("Sessions", "Targets", "Models", "Settings", "Investigation"):
        expected = {"Sessions": 1, "Targets": 2, "Models": 3, "Settings": 4, "Investigation": 0}[page]
        window.switch_to_tab(page)
        assert window.stack.currentIndex() == expected
        assert window.sidebar.buttons[page].is_active()
    with pytest.raises(ValueError):
        window.switch_to_tab("Nonexistent")


@pytest.mark.skipif(not qt_available(), reason="PySide6 not installed")
def test_shortcuts_registered(window_factory: Any) -> None:
    window = window_factory(None)
    expected = {"Ctrl+N", "Ctrl+P", "Ctrl+Shift+S", "Ctrl+L", "Ctrl+T", "F5", "Esc"}
    assert set(window._shortcuts) == expected
    assert all(shortcut is not None for shortcut in window._shortcuts.values())


@pytest.mark.skipif(not qt_available(), reason="PySide6 not installed")
def test_sessions_loaded_from_controller(window_factory: Any) -> None:
    controller = FakeController()
    controller.sessions_result = [_summary("10.0.0.5")]
    window = window_factory(controller)
    recorded: list[list[Any]] = []
    window.sessions.set_previous = lambda summaries: recorded.append(list(summaries))  # type: ignore[method-assign]

    async def scenario() -> None:
        window._refresh_sessions()
        await asyncio.sleep(0.05)

    asyncio.run(scenario())
    assert recorded and recorded[0][0].target == "10.0.0.5"


@pytest.mark.skipif(not qt_available(), reason="PySide6 not installed")
def test_findings_button_opens_dialog_with_filter(
    window_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bletiner.ui import desktop

    controller = FakeController()
    controller.current_findings = [_finding(Severity.HIGH, VS.VERIFIED)]
    window = window_factory(controller)
    created: list[Any] = []

    class FakeDialog:
        def __init__(
            self, findings: list[Finding], *, filter_state: str | None = None, parent: Any = None
        ) -> None:
            self.findings = findings
            self.filter_state = filter_state
            created.append(self)

        def setWindowTitle(self, title: str) -> None:
            self.title = title

        def exec(self) -> int:
            return 0

    monkeypatch.setattr(desktop, "_findings_dialog_class", lambda: FakeDialog)
    window._open_findings("confirmed")
    assert created and created[0].filter_state == "confirmed"
    assert created[0].findings[0].severity is Severity.HIGH


@pytest.mark.skipif(not qt_available(), reason="PySide6 not installed")
def test_new_investigation_dialog_applies_values(
    window_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtWidgets import QDialog

    from bletiner.ui import desktop

    controller = FakeController()
    window = window_factory(controller)
    started: list[dict[str, Any]] = []
    controller.start = lambda **kwargs: (started.append(kwargs), _completed_coroutine())[1]  # type: ignore[method-assign]

    class FakeDialog:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        def exec(self) -> Any:
            return QDialog.DialogCode.Accepted

        def target(self) -> str:
            return "10.9.8.7"

        def mode(self) -> str:
            return "fast"

        def engine(self) -> str:
            return "local"

        def provider(self) -> str:
            return ""

        def model(self) -> str:
            return ""

    monkeypatch.setattr(desktop, "_new_investigation_dialog_class", lambda: FakeDialog)
    window.open_new_investigation_dialog()
    assert window.investigation.target() == "10.9.8.7"
    assert window.investigation.mode() == "fast"
    assert started and started[0]["target"] == "10.9.8.7"


async def _completed_coroutine() -> Any:
    return None


@pytest.mark.skipif(not qt_available(), reason="PySide6 not installed")
def test_new_investigation_falls_back_to_targets_page(
    window_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bletiner.ui import desktop

    window = window_factory(None)
    monkeypatch.setattr(desktop, "_new_investigation_dialog_class", lambda: None)
    window.open_new_investigation_dialog()
    assert window.stack.currentIndex() == 2
    assert window.sidebar.buttons["Targets"].is_active()


@pytest.mark.skipif(not qt_available(), reason="PySide6 not installed")
def test_settings_default_target_is_prefilled(window_factory: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from bletiner.ui import desktop

    monkeypatch.setattr(desktop, "_settings_prefill", lambda: ("https://10.0.0.5/", "fast"))
    monkeypatch.setattr(desktop, "_settings_authorized_scope", lambda: ["10.0.0.0/8"])
    controller = FakeController()
    window = window_factory(controller)
    assert window.investigation.target() == "10.0.0.5"
    assert window.investigation.mode() == "fast"
    assert "10.0.0.5" in window.top_bar.capsule_text.text()
    assert window.investigation.scope_hint.isHidden() is True


@pytest.mark.skipif(not qt_available(), reason="PySide6 not installed")
def test_scope_hint_warns_outside_authorized_scope(
    window_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bletiner.ui import desktop

    monkeypatch.setattr(desktop, "_settings_authorized_scope", lambda: ["10.0.0.0/8"])
    controller = FakeController()
    controller.target_in_authorized_scope = (  # type: ignore[method-assign]
        lambda cleaned, authorized: str(cleaned).startswith("10.")
    )
    window = window_factory(controller)
    window.investigation.set_target("192.168.5.5")
    assert window.investigation.scope_hint.isHidden() is False
    assert "outside the authorized scope" in window.investigation.scope_hint.text()
    window.investigation.set_target("10.1.2.3")
    assert window.investigation.scope_hint.isHidden() is True


@pytest.mark.skipif(not qt_available(), reason="PySide6 not installed")
def test_probe_checking_state_is_never_a_false_negative(window_factory: Any) -> None:
    controller = FakeController(FakeReadiness(probe_complete=False))
    window = window_factory(controller)
    assert window.investigation.btn_start.isEnabled() is False
    assert "Checking sandbox readiness" in window.investigation.banner.text.text()
    assert "checking" in window.footer.docker_label.text()
    assert "checking" in window.footer.gate_label.text()
    assert "Checking" in window.sidebar.docker_status.text()


@pytest.mark.skipif(not qt_available(), reason="PySide6 not installed")
def test_ai_toggle_explains_key_vs_gate_block(window_factory: Any) -> None:
    controller = FakeController()
    window = window_factory(controller)
    assert window.investigation.btn_ai.isEnabled() is False
    assert "key" in window.investigation.btn_ai.toolTip().lower()
    assert window.investigation.engine_hint.isHidden() is False
    controller.set_readiness(ai_ready=True, provider="deepseek", model="deepseek-chat", gate_certified=False)
    window.refresh_readiness()
    assert window.investigation.btn_ai.isEnabled() is False
    assert "gate" in window.investigation.btn_ai.toolTip().lower()


@pytest.mark.skipif(not qt_available(), reason="PySide6 not installed")
def test_targets_panel_selection_applies_target_and_starts(window_factory: Any) -> None:
    controller = FakeController()
    window = window_factory(controller)
    window.targets_panel.target_selected.emit("10.2.2.2", "deep")
    assert window._current_target == "10.2.2.2"
    assert window.stack.currentIndex() == 0
    assert window.investigation.target() == "10.2.2.2"
