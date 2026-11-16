"""PySide6 desktop shell: real-data investigation workspace.

Layout (mirrors the reference SOC deck):

* Left sidebar dock  -- navigation (Investigation / Sessions / Targets / Models / Settings), real Docker
  readiness badge, version.
* Top bar            -- brand + full subtitle, real target/mode capsule, one 'New investigation' action,
  truthful agent-state pill. Native window frame with a dark DWM title bar (no fake window buttons).
* Investigation page -- 3-card deck: Investigation card (target/mode/engine, readiness banner, one Start,
  real phase progress, real activity feed, real finding badges) + Kali terminal card + Sessions & Files
  card; below it the operator bar (state pill, steering, Pause/Resume, Stop with confirmation).
* Sessions / Targets / Models / Settings pages live in the same shell.
* Footer telemetry -- Docker state, engine, model, gate certification and secret backend.

Everything displayed is derived from the controller, the tool stream, the session store or the readiness
probe. There is no demo data, no simulated progress and no invented model name anywhere in this module.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import inspect
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QCursor, QFont, QKeySequence, QShortcut, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from bletiner import __version__
from bletiner.agent.events import AgentEvent, AgentState, EventType
from bletiner.models.entities import Finding
from bletiner.models.redaction import redact
from bletiner.ui import theme, viewmodels
from bletiner.ui.sessions_card import SessionsFilesCard
from bletiner.ui.terminal_card import TerminalCard

# Pages in sidebar / stack order.
_PAGES: tuple[str, ...] = ("Investigation", "Sessions", "Targets", "Models", "Settings")
_PAGE_INDEX = {name.lower(): index for index, name in enumerate(_PAGES)}

_APP_TITLE = "Bletiner AI \u2014 Autonomous Security Investigation Workspace"
_SUBTITLE = "Autonomous Security Investigation Workspace"


# ---------------------------------------------------------------------------------------------------------
# Small reusable widgets
# ---------------------------------------------------------------------------------------------------------
def _icon_label(name: str, size: int = 16, color: str = theme.TEXT_SECONDARY) -> QLabel:
    """QLabel showing a named Segoe MDL2 glyph (theme falls back on non-Windows hosts)."""
    label = QLabel(theme.icon_char(name))
    label.setStyleSheet(theme.icon_css(size, color))
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    return label


def _pill(text: str, variant: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet(theme.pill_qss(variant))
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    return label


def _role_label(text: str, role: str = "muted", *, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", role)
    label.setWordWrap(wrap)
    return label


def _card_header(icon_name: str, text: str) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(8)
    row.addWidget(_icon_label(icon_name, 15, theme.ACCENT_BLUE))
    header = QLabel(text)
    header.setProperty("role", "cardHeader")
    header.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    row.addWidget(header)
    row.addStretch(1)
    return row


def _section_header(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", "sectionHeader")
    return label


class ElidedLabel(QLabel):
    """Single-line QLabel that elides its text to the available width (tooltip keeps the full text)."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = ""
        self._ellipsis_width = 2
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.set_full_text(text)

    def set_full_text(self, text: str) -> None:
        self._full_text = str(text)
        self.setToolTip(self._full_text)
        self._apply_elide()

    def full_text(self) -> str:
        return self._full_text

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self) -> None:
        width = max(0, self.width() - self._ellipsis_width)
        if width <= 0:
            QLabel.setText(self, self._full_text)
            return
        elided = self.fontMetrics().elidedText(self._full_text, Qt.TextElideMode.ElideRight, width)
        QLabel.setText(self, elided)


class NavButton(QPushButton):
    """Sidebar navigation button with a crisp icon glyph above its label."""

    def __init__(self, icon_name: str, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("navBtn")
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setFixedSize(76, 58)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 7, 0, 6)
        layout.setSpacing(2)
        self.icon = QLabel(theme.icon_char(icon_name))
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.label = QLabel(label)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.icon)
        layout.addWidget(self.label)
        self._active = False
        self.set_active(False)

    def set_active(self, active: bool) -> None:
        self._active = active
        if active:
            background = "rgba(37, 99, 235, 0.18)"
            border = "1px solid #2563eb"
            icon_color = "#93c5fd"
            text_color = "#bfdbfe"
        else:
            background = "transparent"
            border = "1px solid transparent"
            icon_color = theme.TEXT_SECONDARY
            text_color = theme.TEXT_SECONDARY
        self.setStyleSheet(
            f"QPushButton#navBtn {{ background-color: {background}; border: {border}; border-radius: 10px; }}"
            "QPushButton#navBtn:hover { background-color: rgba(255, 255, 255, 0.05); }"
        )
        family = theme.icon_font_family()
        family_css = f"font-family: '{family}';" if family else ""
        self.icon.setStyleSheet(
            f"{family_css} font-size: 19px; color: {icon_color}; background: transparent;"
        )
        self.label.setStyleSheet(
            f"color: {text_color}; font-size: 9px; font-weight: {700 if active else 600}; background: transparent;"
        )

    def is_active(self) -> bool:
        return self._active


class FindingsButton(QPushButton):
    """Findings badge button: icon, label, live count and a chevron."""

    def __init__(self, icon_name: str, object_name: str, label: str, color: str) -> None:
        super().__init__()
        self.setObjectName(object_name)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setMinimumHeight(36)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(8)
        icon = QLabel(theme.icon_char(icon_name))
        icon.setStyleSheet(theme.icon_css(14, color))
        icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.label = QLabel(label)
        self.label.setStyleSheet(
            f"color: {color}; font-size: 12px; font-weight: 600; background: transparent;"
        )
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.count = QLabel("0")
        self.count.setStyleSheet(
            f"color: {color}; font-size: 12px; font-weight: 700; background: transparent;"
        )
        self.count.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        chevron = QLabel("\u203a")
        chevron.setStyleSheet(f"color: {color}; font-size: 16px; font-weight: 700; background: transparent;")
        chevron.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(icon)
        layout.addWidget(self.label)
        layout.addWidget(self.count)
        layout.addStretch(1)
        layout.addWidget(chevron)
        self.set_setting = label

    def set_count(self, count: int) -> None:
        self.count.setText(str(max(0, int(count))))
        self.setAccessibleName(f"{self.set_setting}: {count}")


class ReadinessBanner(QFrame):
    """Actionable prerequisite banner (Docker offline / gate uncertified / AI key missing)."""

    retry_requested = Signal()
    settings_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("readinessBanner")
        self.setVisible(False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.icon = _icon_label("warning", 14, theme.YELLOW_FG)
        top.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        self.text = QLabel("")
        self.text.setObjectName("readinessText")
        self.text.setWordWrap(True)
        self.text.setTextFormat(Qt.TextFormat.RichText)
        self.text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        top.addWidget(self.text, 1)
        layout.addLayout(top)
        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        buttons.addStretch(1)
        self.btn_retry = QPushButton("Retry")
        self.btn_retry.setObjectName("btnSecondary")
        self.btn_retry.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_retry.setFixedHeight(24)
        self.btn_retry.clicked.connect(self.retry_requested.emit)
        buttons.addWidget(self.btn_retry)
        self.btn_settings = QPushButton("Open Settings")
        self.btn_settings.setObjectName("btnSecondary")
        self.btn_settings.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_settings.setFixedHeight(24)
        self.btn_settings.clicked.connect(self.settings_requested.emit)
        buttons.addWidget(self.btn_settings)
        layout.addLayout(buttons)

    def show_issues(self, issues: list[viewmodels.ReadinessIssue]) -> None:
        if not issues:
            self.setVisible(False)
            return
        severity = "info"
        if any(issue.severity == "error" for issue in issues):
            severity = "error"
        elif any(issue.severity == "warning" for issue in issues):
            severity = "warning"
        self.setProperty("severity", severity)
        self.style().unpolish(self)
        self.style().polish(self)
        self.icon.setText(theme.icon_char("error" if severity == "error" else "warning"))
        color = theme.RED_FG if severity == "error" else theme.YELLOW_FG
        self.icon.setStyleSheet(theme.icon_css(14, color))
        rendered = issues[:2]
        lines = [
            f"<b>{html.escape(issue.title)}</b><br/><span style='color:#94a3b8'>{html.escape(issue.detail)}</span>"
            for issue in rendered
        ]
        extra = len(issues) - len(rendered)
        if extra:
            lines.append(f"<span style='color:#94a3b8'>+{extra} more prerequisite(s)</span>")
        self.text.setText("<br/><br/>".join(lines))
        self.btn_settings.setVisible(any(issue.action in ("settings", "retry") for issue in issues))
        self.setVisible(True)


# ---------------------------------------------------------------------------------------------------------
# Readiness snapshot (duck-typed controller Readiness)
# ---------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ReadinessSnapshot:
    """UI copy of the controller readiness snapshot; 'available' is False when no controller is attached."""

    available: bool = False
    checking: bool = False
    docker_running: bool = False
    gate_certified: bool = False
    kali_container: str | None = None
    secret_backend: str = "unknown"  # noqa: S105 - backend label, not a credential
    ai_ready: bool = False
    provider: str = ""
    model: str = ""
    ollama_running: bool = False
    notes: tuple[str, ...] = ()
    probe_complete: bool = False

    @property
    def local_ready(self) -> bool:
        """Deterministic local engine: real tools need Docker + the Kali container (no egress gate)."""
        return self.docker_running and self.kali_container is not None

    @property
    def ai_engine_ready(self) -> bool:
        """Autonomous AI engine: a provider key plus the certified egress gate and the sandbox."""
        return self.ai_ready and self.gate_certified and self.local_ready


def _snapshot_from_readiness(raw: Any) -> ReadinessSnapshot:
    notes_raw = getattr(raw, "notes", ())
    notes = tuple(str(note) for note in notes_raw) if isinstance(notes_raw, (list, tuple)) else ()
    docker_running = bool(getattr(raw, "docker_running", False))
    probe_complete = bool(getattr(raw, "probe_complete", False))
    checking = not probe_complete
    gate_certified = bool(getattr(raw, "gate_certified", False))
    kali_container = getattr(raw, "kali_container", None)
    return ReadinessSnapshot(
        available=True,
        checking=checking,
        docker_running=docker_running,
        gate_certified=gate_certified,
        kali_container=str(kali_container).lstrip("/") if kali_container else None,
        secret_backend=str(getattr(raw, "secret_backend", "unknown") or "unknown"),
        ai_ready=bool(getattr(raw, "ai_ready", False)),
        provider=str(getattr(raw, "provider", "") or ""),
        model=str(getattr(raw, "model", "") or ""),
        ollama_running=bool(getattr(raw, "ollama_running", False)),
        notes=notes,
        probe_complete=probe_complete,
    )


# ---------------------------------------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------------------------------------
class SidebarDock(QFrame):
    """Left navigation dock with real Docker readiness and version."""

    tab_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sidebarDock")
        self.setFixedWidth(92)
        column = QVBoxLayout(self)
        column.setContentsMargins(8, 14, 8, 12)
        column.setSpacing(6)

        brand = QFrame()
        brand.setObjectName("sidebarBrand")
        brand.setFixedSize(56, 56)
        brand_layout = QVBoxLayout(brand)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.addWidget(_icon_label("shield", 26, theme.ACCENT_BLUE))
        column.addWidget(brand, 0, Qt.AlignmentFlag.AlignHCenter)
        column.addSpacing(12)

        self.buttons: dict[str, NavButton] = {}
        nav_items = (
            ("Investigation", "home", "Investigation"),
            ("Sessions", "history", "Sessions"),
            ("Targets", "target", "Targets"),
            ("Models", "models", "Models"),
            ("Settings", "settings", "Settings"),
        )
        for key, icon_name, label in nav_items:
            button = NavButton(icon_name, label)
            button.setToolTip(label)
            button.clicked.connect(lambda _checked=False, page=key: self._on_click(page))
            self.buttons[key] = button
            column.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)
        column.addStretch(1)

        self.docker_badge = QPushButton()
        self.docker_badge.setObjectName("dockerBadge")
        self.docker_badge.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.docker_badge.setFixedSize(76, 46)
        self.docker_badge.setToolTip("Refresh Docker / Kali sandbox readiness")
        badge_layout = QVBoxLayout(self.docker_badge)
        badge_layout.setContentsMargins(2, 5, 2, 5)
        badge_layout.setSpacing(1)
        self.docker_dot = QLabel("\u25cf")
        self.docker_dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.docker_dot.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        badge_layout.addWidget(self.docker_dot)
        self.docker_caption = QLabel("Docker")
        self.docker_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.docker_caption.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        badge_layout.addWidget(self.docker_caption)
        self.docker_status = QLabel("not checked")
        self.docker_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.docker_status.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        badge_layout.addWidget(self.docker_status)
        column.addWidget(self.docker_badge, 0, Qt.AlignmentFlag.AlignHCenter)
        column.addSpacing(6)

        self.version = QLabel(f"v{__version__}")
        self.version.setObjectName("sidebarVersion")
        self.version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(self.version, 0, Qt.AlignmentFlag.AlignHCenter)

        self.set_readiness(ReadinessSnapshot(available=False))
        self.select_tab("Investigation")

    def _on_click(self, key: str) -> None:
        self.select_tab(key)
        self.tab_selected.emit(key)

    def select_tab(self, key: str) -> None:
        for name, button in self.buttons.items():
            button.set_active(name == key)

    def set_readiness(self, snapshot: ReadinessSnapshot) -> None:
        text, variant = viewmodels.readiness_pill(
            snapshot.available, snapshot.docker_running, snapshot.kali_container
        )
        colors = {
            "green": theme.GREEN_FG,
            "red": theme.RED_FG,
            "yellow": theme.YELLOW_FG,
            "gray": theme.TEXT_MUTED,
        }
        color = colors.get(variant, theme.TEXT_MUTED)
        status = (
            "Running" if snapshot.docker_running else ("Not checked" if not snapshot.available else "Offline")
        )
        if snapshot.docker_running and snapshot.kali_container is None:
            status = "No Kali"
        if snapshot.checking:
            status = "Checking"
            color = theme.YELLOW_FG
        self.docker_dot.setStyleSheet(f"color: {color}; font-size: 12px; background: transparent;")
        self.docker_status.setStyleSheet(
            f"color: {color}; font-size: 9px; font-weight: 700; background: transparent;"
        )
        self.docker_status.setText(status)
        self.docker_caption.setStyleSheet("color: #8892b0; font-size: 9px; background: transparent;")
        tooltip = text + (("\n" + "\n".join(snapshot.notes)) if snapshot.notes else "")
        self.docker_badge.setToolTip(tooltip)


# ---------------------------------------------------------------------------------------------------------
# Top bar
# ---------------------------------------------------------------------------------------------------------
class TopBar(QFrame):
    """Brand, real target/mode capsule, one 'New investigation' action and the truthful state pill."""

    new_investigation_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("topBar")
        self.setFixedHeight(68)
        row = QHBoxLayout(self)
        row.setContentsMargins(18, 10, 18, 10)
        row.setSpacing(14)

        brand = QVBoxLayout()
        brand.setSpacing(1)
        self.title = QLabel("Bletiner AI")
        self.title.setObjectName("brandTitle")
        self.subtitle = QLabel(_SUBTITLE)
        self.subtitle.setObjectName("brandSubtitle")
        brand.addWidget(self.title)
        brand.addWidget(self.subtitle)
        row.addLayout(brand)
        row.addStretch(1)

        self.capsule = QFrame()
        self.capsule.setObjectName("targetCapsule")
        self.capsule.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.capsule.setMinimumWidth(250)
        self.capsule.setFixedHeight(36)
        capsule_row = QHBoxLayout(self.capsule)
        capsule_row.setContentsMargins(14, 0, 16, 0)
        capsule_row.setSpacing(9)
        capsule_row.addWidget(_icon_label("target", 14, theme.ACCENT_BLUE))
        self.capsule_text = QLabel("")
        self.capsule_text.setObjectName("capsuleText")
        self.capsule_text.setTextFormat(Qt.TextFormat.RichText)
        self.capsule_text.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        capsule_row.addWidget(self.capsule_text)
        row.addWidget(self.capsule)

        self.btn_new = QPushButton("+  New investigation")
        self.btn_new.setObjectName("btnNewInvestigation")
        self.btn_new.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_new.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_new.setToolTip("Configure a new investigation target (Ctrl+N)")
        self.btn_new.clicked.connect(self.new_investigation_requested.emit)
        row.addWidget(self.btn_new)

        self.status_pill = _pill("\u25cf Idle", "gray")
        self.status_pill.setMinimumWidth(104)
        row.addWidget(self.status_pill)

        self.set_target_mode("", "deep")
        self.set_state(AgentState.IDLE)

    def set_target_mode(self, target: str, mode: str) -> None:
        if target:
            target_html = f"<span style='color:#f3f4f6; font-weight:600'>{html.escape(target)}</span>"
        else:
            target_html = "<span style='color:#64748b'>not set</span>"
        mode_text = viewmodels.mode_label(mode).replace(" investigation", "").replace(" discovery", "")
        mode_html = f"<span style='color:#f3f4f6; font-weight:600'>{html.escape(mode_text)}</span>"
        self.capsule_text.setText(
            "<span style='color:#8892b0'>Target:</span> "
            + target_html
            + " <span style='color:#2b3448'>&nbsp;|&nbsp;</span> "
            + "<span style='color:#8892b0'>Mode:</span> "
            + mode_html
        )

    def set_state(self, state: AgentState) -> None:
        variant = viewmodels.state_pill_variant(state)
        self.status_pill.setText("\u25cf " + viewmodels.status_label(state))
        self.status_pill.setStyleSheet(theme.pill_qss(variant))


# ---------------------------------------------------------------------------------------------------------
# Investigation card
# ---------------------------------------------------------------------------------------------------------
class InvestigationCard(QFrame):
    """Target/mode/engine configuration, readiness, one Start, real progress, feed and findings."""

    start_requested = Signal()
    retry_requested = Signal()
    open_settings_requested = Signal()
    findings_requested = Signal(str)
    engine_changed = Signal(str)
    target_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("investigationCard")
        self.setMinimumWidth(340)
        self.setMaximumWidth(430)
        self._snapshot = ReadinessSnapshot(available=False)
        self._running = False
        self._max_feed_rows = 500
        self._feed_placeholder: QListWidgetItem | None = None
        self._tool_count = 0
        self._finding_count = 0
        self._start_block: str | None = None

        column = QVBoxLayout(self)
        column.setContentsMargins(14, 12, 14, 12)
        column.setSpacing(8)
        column.addLayout(_card_header("target", "INVESTIGATION"))

        labels = QHBoxLayout()
        labels.setSpacing(8)
        labels.addWidget(_section_header("Target"), 3)
        labels.addWidget(_section_header("Mode"), 2)
        column.addLayout(labels)

        inputs = QHBoxLayout()
        inputs.setSpacing(8)
        self.target_edit = QLineEdit()
        self.target_edit.setPlaceholderText("Domain or IPv4 address")
        self.target_edit.setClearButtonEnabled(True)
        self.target_edit.setToolTip("Authorized target domain or IP address")
        inputs.addWidget(self.target_edit, 3)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Deep", "deep")
        self.mode_combo.addItem("Fast", "fast")
        self.mode_combo.setMinimumWidth(86)
        inputs.addWidget(self.mode_combo, 2)
        column.addLayout(inputs)
        self.target_edit.textChanged.connect(self._on_target_text_changed)

        self.scope_hint = _role_label("", "hint", wrap=True)
        self.scope_hint.setVisible(False)
        column.addWidget(self.scope_hint)

        engine_header = QHBoxLayout()
        engine_header.addWidget(_section_header("Engine"))
        engine_header.addStretch(1)
        column.addLayout(engine_header)

        engine_row = QHBoxLayout()
        engine_row.setSpacing(8)
        self.btn_local = QPushButton("Local recon")
        self.btn_local.setProperty("engineToggle", True)
        self.btn_local.setObjectName("engineToggle")
        self.btn_local.setCheckable(True)
        self.btn_local.setChecked(True)
        self.btn_local.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_local.setToolTip(
            "Deterministic recon with real tools in the Kali sandbox. No API key needed."
        )
        self.btn_ai = QPushButton("AI agent")
        self.btn_ai.setProperty("engineToggle", True)
        self.btn_ai.setObjectName("engineToggle")
        self.btn_ai.setCheckable(True)
        self.btn_ai.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_ai.setToolTip("Autonomous agent using the configured provider and API key.")
        self.engine_group = QButtonGroup(self)
        self.engine_group.setExclusive(True)
        self.engine_group.addButton(self.btn_local)
        self.engine_group.addButton(self.btn_ai)
        engine_row.addWidget(self.btn_local, 1)
        engine_row.addWidget(self.btn_ai, 1)
        column.addLayout(engine_row)
        self.engine_hint = _role_label("", "hint", wrap=True)
        self.engine_hint.setVisible(False)
        column.addWidget(self.engine_hint)
        self.btn_local.toggled.connect(self._on_engine_toggled)
        self.btn_ai.toggled.connect(self._on_engine_toggled)

        self.banner = ReadinessBanner()
        self.banner.retry_requested.connect(self.retry_requested.emit)
        self.banner.settings_requested.connect(self.open_settings_requested.emit)
        column.addWidget(self.banner)

        self.error_banner = QFrame()
        self.error_banner.setObjectName("errorBanner")
        self.error_banner.setVisible(False)
        error_layout = QHBoxLayout(self.error_banner)
        error_layout.setContentsMargins(10, 6, 10, 6)
        error_layout.setSpacing(8)
        error_layout.addWidget(_icon_label("error", 13, theme.RED_FG))
        self.error_text = QLabel("")
        self.error_text.setObjectName("errorBannerText")
        self.error_text.setWordWrap(True)
        self.error_text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        error_layout.addWidget(self.error_text, 1)
        self.btn_error_close = QPushButton("\u2715")
        self.btn_error_close.setObjectName("iconButton")
        self.btn_error_close.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_error_close.setFixedSize(22, 22)
        self.btn_error_close.setToolTip("Dismiss error")
        self.btn_error_close.clicked.connect(self.clear_error)
        error_layout.addWidget(self.btn_error_close)
        column.addWidget(self.error_banner)

        self.btn_start = QPushButton("Start investigation")
        self.btn_start.setObjectName("btnPrimary")
        self.btn_start.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_start.setMinimumHeight(38)
        self.btn_start.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_start.clicked.connect(self.start_requested.emit)
        column.addWidget(self.btn_start)
        self.start_hint = _role_label("", "hint", wrap=True)
        column.addWidget(self.start_hint)

        progress_labels = QHBoxLayout()
        progress_labels.setSpacing(8)
        progress_labels.addWidget(_section_header("Progress"))
        progress_labels.addStretch(1)
        self.phase_label = ElidedLabel("Idle")
        self.phase_label.setProperty("role", "muted")
        self.phase_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        progress_labels.addWidget(self.phase_label, 2)
        self.percent_label = QLabel("0%")
        self.percent_label.setProperty("role", "metric")
        progress_labels.addWidget(self.percent_label)
        column.addLayout(progress_labels)

        self.progress = QProgressBar()
        self.progress.setObjectName("progressBar")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        column.addWidget(self.progress)

        feed_header = QHBoxLayout()
        feed_header.setSpacing(8)
        feed_header.addWidget(_icon_label("activity", 13, theme.ACCENT_BLUE))
        feed_header.addWidget(_section_header("Activity"))
        feed_header.addStretch(1)
        self.activity_count = _role_label("no events yet", "hint")
        feed_header.addWidget(self.activity_count)
        column.addLayout(feed_header)

        self.feed_list = QListWidget()
        self.feed_list.setObjectName("feedList")
        self.feed_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.feed_list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.feed_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.feed_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.feed_list.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.feed_list.setMinimumHeight(120)
        column.addWidget(self.feed_list, 1)
        self._show_feed_placeholder()

        findings_header = QHBoxLayout()
        findings_header.setSpacing(8)
        findings_header.addWidget(_icon_label("flag", 13, theme.YELLOW_FG))
        findings_header.addWidget(_section_header("Findings"))
        findings_header.addStretch(1)
        self.findings_hint = _role_label("", "hint")
        findings_header.addWidget(self.findings_hint)
        column.addLayout(findings_header)

        self.btn_potential = FindingsButton("warning", "btnFindingsPotential", "Potential", theme.YELLOW_FG)
        self.btn_potential.clicked.connect(lambda: self.findings_requested.emit("potential"))
        column.addWidget(self.btn_potential)
        self.btn_confirmed = FindingsButton("bug", "btnFindingsConfirmed", "Confirmed", theme.RED_FG)
        self.btn_confirmed.clicked.connect(lambda: self.findings_requested.emit("confirmed"))
        column.addWidget(self.btn_confirmed)

        self.apply_readiness(self._snapshot)

    # -- configuration ---------------------------------------------------------------------------------
    def target(self) -> str:
        return self.target_edit.text().strip()

    def set_target(self, target: str) -> None:
        self.target_edit.setText(target or "")

    def mode(self) -> str:
        return str(self.mode_combo.currentData() or "deep")

    def set_mode(self, mode: str) -> None:
        index = self.mode_combo.findData("deep" if str(mode).lower().startswith("deep") else "fast")
        self.mode_combo.setCurrentIndex(max(0, index))

    def engine(self) -> str:
        return "ai" if self.btn_ai.isChecked() else "local"

    def set_engine(self, engine: str) -> None:
        button = self.btn_ai if str(engine).lower() == "ai" else self.btn_local
        if button.isEnabled():
            button.setChecked(True)

    def _on_target_text_changed(self, text: str) -> None:
        self.target_changed.emit(text.strip())

    def set_scope_hint(self, text: str, *, severity: str = "warning") -> None:
        """Show/hide the target scope/validation hint under the target field."""
        if not text:
            self.scope_hint.setVisible(False)
            self.scope_hint.setText("")
            return
        color = {"warning": theme.YELLOW_FG, "error": theme.RED_FG}.get(severity, theme.TEXT_MUTED)
        self.scope_hint.setStyleSheet(
            f"color: {color}; font-size: 10px; background: transparent; border: none;"
        )
        self.scope_hint.setText(text)
        self.scope_hint.setVisible(True)

    def _on_engine_toggled(self, _checked: bool) -> None:
        if not self.btn_local.isChecked() and not self.btn_ai.isChecked():
            self.btn_local.setChecked(True)
            return
        self._sync_engine_ui()
        self.engine_changed.emit(self.engine())

    # -- readiness / run state -------------------------------------------------------------------------
    def apply_readiness(self, snapshot: ReadinessSnapshot) -> None:
        self._snapshot = snapshot
        issues = viewmodels.readiness_issues(
            available=snapshot.available,
            checking=snapshot.checking,
            docker_running=snapshot.docker_running,
            gate_certified=snapshot.gate_certified,
            kali_container=snapshot.kali_container,
            ai_ready=snapshot.ai_ready,
            engine=self.engine(),
            provider=snapshot.provider,
            model=snapshot.model,
        )
        self.banner.show_issues(issues)
        self._sync_engine_ui()

    def _sync_engine_ui(self) -> None:
        snapshot = self._snapshot
        ai_block = viewmodels.ai_engine_block_reason(
            docker_running=snapshot.docker_running,
            gate_certified=snapshot.gate_certified,
            kali_container=snapshot.kali_container,
            ai_ready=snapshot.ai_ready,
        )
        ai_enabled = ai_block is None and snapshot.available and not snapshot.checking
        self.btn_ai.setEnabled(ai_enabled)
        self.btn_ai.setToolTip(
            "Autonomous agent using the configured provider and API key."
            if ai_enabled
            else f"AI engine unavailable: {ai_block or 'sandbox readiness is still being checked'}."
        )
        if not snapshot.available or snapshot.checking or ai_block is None:
            self.engine_hint.setText("")
            self.engine_hint.setVisible(False)
        else:
            self.engine_hint.setText(f"AI: {ai_block}")
            self.engine_hint.setVisible(True)
        block = viewmodels.start_block_reason(
            available=snapshot.available,
            checking=snapshot.checking,
            docker_running=snapshot.docker_running,
            gate_certified=snapshot.gate_certified,
            kali_container=snapshot.kali_container,
            ai_ready=snapshot.ai_ready,
            engine=self.engine(),
        )
        self._start_block = block
        self.btn_start.setEnabled(block is None and not self._running)
        if block:
            self.start_hint.setText("Start is disabled until the prerequisites above are resolved.")
        elif self.engine() == "ai":
            self.start_hint.setText("Ready \u2014 real tools run through the certified egress gate.")
        else:
            self.start_hint.setText("Ready \u2014 real recon tools, no API key required.")

    def set_running(self, running: bool) -> None:
        self._running = running
        for widget in (self.target_edit, self.mode_combo, self.btn_local, self.btn_ai):
            widget.setEnabled(not running)
        if running:
            self.btn_start.setText("Running\u2026")
            self.btn_start.setEnabled(False)
        else:
            self.btn_start.setText("Start investigation")
            self._sync_engine_ui()
        self.btn_potential.setEnabled(not running)
        self.btn_confirmed.setEnabled(not running)

    def set_progress(self, percent: int, phase: str) -> None:
        percent = max(0, min(100, int(percent)))
        self.progress.setValue(percent)
        self.percent_label.setText(f"{percent}%")
        self.phase_label.set_full_text(phase)

    def apply_state(self, state: AgentState) -> None:
        running = viewmodels.is_running_state(state)
        self.set_running(running)
        current = self.progress.value()
        percent = viewmodels.progress_for_state(state, current)
        phase = viewmodels.phase_description(state, tools=self._tool_count, findings=self._finding_count)
        self.set_progress(percent, phase)

    def set_counters(self, *, tools: int, findings: int) -> None:
        self._tool_count = tools
        self._finding_count = findings

    def set_phase(self, phase: str) -> None:
        self.phase_label.set_full_text(phase)

    # -- feed ------------------------------------------------------------------------------------------
    def _show_feed_placeholder(self) -> None:
        self.feed_list.clear()
        placeholder = QListWidgetItem("No activity yet \u2014 start an investigation.")
        placeholder.setForeground(Qt.GlobalColor.gray)
        font = QFont()
        font.setPointSizeF(8.5)
        placeholder.setFont(font)
        placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
        self.feed_list.addItem(placeholder)
        self._feed_placeholder = placeholder

    def clear_feed(self) -> None:
        self._show_feed_placeholder()
        self.activity_count.setText("no events yet")

    def add_feed_row(self, row: viewmodels.FeedRow) -> None:
        if self._feed_placeholder is not None:
            self.feed_list.clear()
            self._feed_placeholder = None
        item = QListWidgetItem()
        item.setSizeHint(QSize(10, 26))
        container = QWidget()
        container.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(4, 1, 6, 1)
        layout.setSpacing(7)
        glyph = QLabel(row.glyph)
        glyph.setFixedWidth(14)
        glyph.setStyleSheet(
            f"color: {viewmodels.feed_row_color(row.kind)}; font-size: 11px; font-weight: 700; background: transparent;"
        )
        glyph.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        text = ElidedLabel(row.text)
        text.setStyleSheet("color: #cbd5e1; font-size: 10px; background: transparent;")
        timestamp = QLabel(row.timestamp)
        timestamp.setStyleSheet("color: #566179; font-size: 9px; background: transparent;")
        timestamp.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(glyph)
        layout.addWidget(text, 1)
        layout.addWidget(timestamp)
        self.feed_list.addItem(item)
        self.feed_list.setItemWidget(item, container)
        while self.feed_list.count() > self._max_feed_rows:
            self.feed_list.takeItem(0)
        self.activity_count.setText(f"{self.feed_list.count()} events")
        scrollbar = self.feed_list.verticalScrollBar()
        if scrollbar is not None:
            scrollbar.setValue(scrollbar.maximum())

    # -- findings / errors -----------------------------------------------------------------------------
    def set_badges(self, findings: list[Finding]) -> None:
        badges = viewmodels.FindingBadges.from_findings(findings)
        self.btn_potential.set_count(badges.potential)
        self.btn_confirmed.set_count(badges.confirmed)
        tooltip = viewmodels.badge_tooltip(badges)
        self.btn_potential.setToolTip("Potential findings (not verified).\n" + tooltip)
        self.btn_confirmed.setToolTip("Confirmed findings (verified).\n" + tooltip)
        total = badges.potential + badges.confirmed
        self.findings_hint.setText(f"{total} total" if total else "none yet")
        self._finding_count = total

    def show_error(self, message: str) -> None:
        self.error_text.setText(str(message))
        self.error_banner.setVisible(True)

    def clear_error(self) -> None:
        self.error_banner.setVisible(False)
        self.error_text.setText("")


# ---------------------------------------------------------------------------------------------------------
# Operator bar
# ---------------------------------------------------------------------------------------------------------
class BottomBar(QFrame):
    """Operator bar: truthful state pill, steering input, Pause/Resume and Stop (confirmed)."""

    steering_submitted = Signal(str)
    pause_clicked = Signal()
    stop_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("bottomControlCard")
        self.setFixedHeight(62)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 10, 14, 10)
        row.setSpacing(12)

        self.state_pill = _pill("\u25cf Idle", "gray")
        self.state_pill.setMinimumWidth(110)
        row.addWidget(self.state_pill)

        self.steering_container = QFrame()
        self.steering_container.setObjectName("steeringContainer")
        steering_layout = QHBoxLayout(self.steering_container)
        steering_layout.setContentsMargins(14, 0, 6, 0)
        steering_layout.setSpacing(6)
        steering_layout.addWidget(_icon_label("send", 13, theme.ACCENT_BLUE))
        self.steering_input = QLineEdit()
        self.steering_input.setObjectName("steeringInput")
        self.steering_input.setPlaceholderText("Start an investigation to steer it\u2026")
        self.steering_input.returnPressed.connect(self._emit_steering)
        self.steering_input.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        steering_layout.addWidget(self.steering_input, 1)
        self.btn_send = QPushButton("\u27a4")
        self.btn_send.setObjectName("iconButton")
        self.btn_send.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_send.setFixedSize(28, 28)
        self.btn_send.setToolTip("Send steering instruction (Ctrl+L)")
        self.btn_send.clicked.connect(self._emit_steering)
        steering_layout.addWidget(self.btn_send)
        row.addWidget(self.steering_container, 1)

        self.btn_pause = QPushButton("Pause")
        self.btn_pause.setObjectName("btnPause")
        self.btn_pause.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_pause.setMinimumWidth(100)
        self.btn_pause.setToolTip("Pause / resume the run (Ctrl+P)")
        self.btn_pause.clicked.connect(self.pause_clicked.emit)
        row.addWidget(self.btn_pause)

        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setObjectName("btnStop")
        self.btn_stop.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_stop.setMinimumWidth(96)
        self.btn_stop.setToolTip("Stop the run (Ctrl+Shift+S, asks for confirmation)")
        self.btn_stop.clicked.connect(self.stop_clicked.emit)
        row.addWidget(self.btn_stop)

        self.set_state(viewmodels.status_label(AgentState.IDLE), "gray")
        self.set_running(False)

    def _emit_steering(self) -> None:
        text = self.steering_input.text().strip()
        if text:
            self.steering_submitted.emit(text)

    def steering_text(self) -> str:
        return self.steering_input.text().strip()

    def clear_steering(self) -> None:
        self.steering_input.clear()

    def set_state(self, text: str, variant: str) -> None:
        self.state_pill.setText("\u25cf " + text)
        self.state_pill.setStyleSheet(theme.pill_qss(variant))

    def set_running(self, running: bool) -> None:
        self.btn_pause.setEnabled(running)
        self.btn_stop.setEnabled(running)
        self.steering_input.setEnabled(False)
        if not running:
            self.btn_pause.setText("Pause")
            self.steering_input.setPlaceholderText("Start an investigation to steer it\u2026")
            self.btn_send.setEnabled(False)

    def set_steering_enabled(self, enabled: bool, placeholder: str) -> None:
        self.steering_input.setEnabled(enabled)
        self.btn_send.setEnabled(enabled)
        self.steering_input.setPlaceholderText(placeholder)

    def set_paused(self, paused: bool) -> None:
        self.btn_pause.setText("Resume" if paused else "Pause")


# ---------------------------------------------------------------------------------------------------------
# Footer telemetry
# ---------------------------------------------------------------------------------------------------------
class FooterBar(QFrame):
    """Bottom telemetry strip: Docker, engine, model, gate certification and secret backend."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("footerBar")
        self.setFixedHeight(28)
        row = QHBoxLayout(self)
        row.setContentsMargins(18, 0, 18, 0)
        row.setSpacing(18)
        self.docker_label = self._make_label()
        self.engine_label = self._make_label()
        self.model_label = self._make_label()
        self.gate_label = self._make_label()
        self.secret_label = self._make_label()
        for label in (
            self.docker_label,
            self.engine_label,
            self.model_label,
            self.gate_label,
            self.secret_label,
        ):
            row.addWidget(label)
        row.addStretch(1)
        self.version_label = self._make_label()
        self.version_label.setText(f"Bletiner AI v{__version__}")
        row.addWidget(self.version_label)
        self.set_readiness(ReadinessSnapshot(available=False))
        self.set_engine("local", "", "")

    def _make_label(self) -> QLabel:
        label = QLabel("")
        label.setProperty("role", "footer")
        label.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
        return label

    def _set(self, label: QLabel, text: str, color: str = theme.TEXT_MUTED) -> None:
        label.setText(text)
        label.setStyleSheet(f"color: {color}; font-size: 10px; background: transparent;")

    def set_readiness(self, snapshot: ReadinessSnapshot) -> None:
        if not snapshot.available:
            self._set(self.docker_label, "\u25cf Docker: not checked", theme.TEXT_MUTED)
            self._set(self.gate_label, "Gate: unknown", theme.TEXT_MUTED)
            self._set(self.secret_label, "Secrets: unknown", theme.TEXT_MUTED)
            return
        if snapshot.checking:
            self._set(self.docker_label, "\u25cf Docker: checking\u2026", theme.YELLOW_FG)
            self._set(self.gate_label, "Gate: checking\u2026", theme.YELLOW_FG)
            self._set(self.secret_label, "Secrets: checking\u2026", theme.YELLOW_FG)
            return
        if snapshot.docker_running and snapshot.kali_container:
            self._set(
                self.docker_label,
                f"\u25cf Docker: Running \u00b7 {snapshot.kali_container}",
                theme.GREEN_FG,
            )
        elif snapshot.docker_running:
            self._set(self.docker_label, "\u25cf Docker: Running \u00b7 no Kali container", theme.YELLOW_FG)
        else:
            self._set(self.docker_label, "\u25cf Docker: Offline", theme.RED_FG)
        self._set(
            self.gate_label,
            "Gate: {}".format("certified" if snapshot.gate_certified else "not certified"),
            theme.GREEN_FG if snapshot.gate_certified else theme.YELLOW_FG,
        )
        self._set(self.secret_label, f"Secrets: {snapshot.secret_backend}", theme.TEXT_MUTED)

    def set_engine(self, engine: str, provider: str, model: str) -> None:
        self._set(self.engine_label, f"Engine: {viewmodels.engine_label(engine)}", theme.ACCENT_BLUE)
        self._set(
            self.model_label,
            f"Model: {viewmodels.model_label(provider, model)}",
            theme.TEXT_MUTED if not (provider or model) else theme.TEXT_SECONDARY,
        )


# ---------------------------------------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------------------------------------
class MainWindow(QMainWindow):
    """Single-window workstation shell (offscreen-safe: construction never touches Docker or the network)."""

    def __init__(self, controller: Any = None) -> None:
        super().__init__()
        self.controller = controller
        self.setWindowTitle(_APP_TITLE)
        self.resize(1360, 860)
        self.setMinimumSize(1180, 720)

        self._state = AgentState.IDLE
        self._running = False
        self._terminal_expanded = False
        self._saved_splitter_sizes: list[int] = []
        self._current_target = ""
        self._current_mode = "deep"
        self._current_engine = "local"
        self._tool_count = 0
        self._findings: list[Finding] = []
        self._readiness = ReadinessSnapshot(available=False)
        self._shown_paths: Any = None
        self._previous_sessions: list[Any] = []
        self._bg_tasks: set[asyncio.Task[Any]] = set()
        self._start_task: asyncio.Task[Any] | None = None
        self._last_applied_outcome: Any = None
        self._error_reported: BaseException | None = None
        self._sessions_panel_loaded = False
        self._shortcuts: dict[str, QShortcut] = {}
        self._dark_titlebar_applied = False
        self._readiness_timer = QTimer(self)
        self._readiness_timer.setInterval(5000)
        self._readiness_timer.timeout.connect(self.refresh_readiness)

        self._build_ui()
        self._wire_signals()
        self._install_shortcuts()
        self._reset_run_counters()
        self._apply_state(AgentState.IDLE)

        if self.controller is not None:
            self._prefill_from_settings()
            self.refresh_readiness()
            self._readiness_timer.start()
            QTimer.singleShot(0, self._initial_async_loads)
        else:
            self._apply_readiness(ReadinessSnapshot(available=False))
            self.investigation.start_hint.setText("Attach an investigation controller to enable scanning.")
            self.terminal.set_connected(False)
            self.sessions.clear_current()

    # -- construction ----------------------------------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("appRoot")
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sidebar = SidebarDock()
        root.addWidget(self.sidebar)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        self.top_bar = TopBar()
        right_layout.addWidget(self.top_bar)

        self.stack = QStackedWidget()
        self.stack.setObjectName("pageStack")
        right_layout.addWidget(self.stack, 1)
        self.footer = FooterBar()
        right_layout.addWidget(self.footer)
        root.addWidget(right, 1)
        self.setCentralWidget(central)

        self._build_investigation_page()
        self._build_sessions_page()
        self._build_targets_page()
        self._build_models_page()
        self._build_settings_page()

    def _build_investigation_page(self) -> None:
        page = QWidget()
        page.setObjectName("pageRoot")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(10)
        self.investigation = InvestigationCard()
        self.terminal = TerminalCard(controller=self.controller)
        self.sessions = SessionsFilesCard()
        self.splitter.addWidget(self.investigation)
        self.splitter.addWidget(self.terminal)
        self.splitter.addWidget(self.sessions)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        self.splitter.setSizes([370, 760, 380])
        layout.addWidget(self.splitter, 1)

        self.bottom_bar = BottomBar()
        layout.addWidget(self.bottom_bar)
        self.stack.addWidget(page)

    def _build_sessions_page(self) -> None:
        from bletiner.ui.panels import SessionsPanel

        self.sessions_panel = SessionsPanel()
        self.stack.addWidget(self.sessions_panel)

    def _build_targets_page(self) -> None:
        from bletiner.ui.panels import TargetsPanel

        self.targets_panel = TargetsPanel()
        self.stack.addWidget(self.targets_panel)

    def _build_models_page(self) -> None:
        from bletiner.ui.panels import ModelsPanel

        self.models_panel = ModelsPanel()
        self.stack.addWidget(self.models_panel)

    def _build_settings_page(self) -> None:
        from bletiner.ui.panels import SettingsPanel

        self.settings_panel = SettingsPanel()
        self.stack.addWidget(self.settings_panel)

    def _wire_signals(self) -> None:
        self.sidebar.tab_selected.connect(self._on_sidebar_tab)
        self.sidebar.docker_badge.clicked.connect(self.refresh_readiness)
        self.top_bar.new_investigation_requested.connect(self.open_new_investigation_dialog)
        self.investigation.start_requested.connect(self._on_start_requested)
        self.investigation.retry_requested.connect(self.refresh_readiness)
        self.investigation.open_settings_requested.connect(lambda: self.switch_to_tab("Settings"))
        self.investigation.findings_requested.connect(self._open_findings)
        self.investigation.engine_changed.connect(self._on_engine_changed)
        self.investigation.target_changed.connect(self._on_target_changed)
        self.bottom_bar.steering_submitted.connect(self._on_steering_submitted)
        self.bottom_bar.pause_clicked.connect(self._on_pause_clicked)
        self.bottom_bar.stop_clicked.connect(self._on_stop_clicked)
        self.terminal.maximize_toggled.connect(self._toggle_terminal_expand)
        self.terminal.command_entered.connect(self._on_terminal_command_entered)
        self.sessions.session_open_requested.connect(self._open_session_findings)

        for emitter, slot in (
            (getattr(self.targets_panel, "target_selected", None), self._on_targets_selected),
            (getattr(self.models_panel, "model_changed", None), self._on_model_changed),
            (getattr(self.settings_panel, "settings_updated", None), self._on_settings_updated),
            (getattr(self.sessions_panel, "session_open_requested", None), self._open_session_findings),
        ):
            if emitter is not None and hasattr(emitter, "connect"):
                with contextlib.suppress(Exception):
                    emitter.connect(slot)

        controller = self.controller
        if controller is not None:
            for attribute, slot in (
                ("on_event", self._on_agent_event),
                ("on_tool_output", self._on_tool_output),
                ("on_state_change", self._on_state_change),
                ("on_completed", self._on_completed),
                ("on_error", self._on_error),
            ):
                emitter = getattr(controller, attribute, None)
                if emitter is not None and hasattr(emitter, "connect"):
                    with contextlib.suppress(Exception):
                        emitter.connect(slot)

    def _install_shortcuts(self) -> None:
        bindings = {
            "Ctrl+N": self.open_new_investigation_dialog,
            "Ctrl+P": self._on_pause_clicked,
            "Ctrl+Shift+S": self._on_stop_clicked,
            "Ctrl+L": self._focus_steering,
            "Ctrl+T": self._focus_terminal,
            "F5": self._on_refresh,
            "Esc": self._on_escape,
        }
        for sequence, slot in bindings.items():
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(slot)
            self._shortcuts[sequence] = shortcut

    # -- readiness -------------------------------------------------------------------------------------
    def refresh_readiness(self) -> None:
        self._apply_readiness(self._readiness_snapshot())

    def _readiness_snapshot(self) -> ReadinessSnapshot:
        controller = self.controller
        if controller is None:
            return ReadinessSnapshot(available=False, notes=("No investigation controller is attached.",))
        readiness = getattr(controller, "readiness", None)
        if not callable(readiness):
            return ReadinessSnapshot(
                available=False, notes=("Controller does not expose a readiness snapshot.",)
            )
        try:
            raw = readiness()
        except Exception as exc:  # pragma: no cover - defensive integration guard
            self._report_problem(f"Readiness probe failed: {exc}")
            return ReadinessSnapshot(available=False, notes=(str(exc),))
        return _snapshot_from_readiness(raw)

    def _apply_readiness(self, snapshot: ReadinessSnapshot) -> None:
        previous_local_ready = self._readiness.local_ready
        self._readiness = snapshot
        self.sidebar.set_readiness(snapshot)
        self.footer.set_readiness(snapshot)
        self.investigation.apply_readiness(snapshot)
        self._sync_terminal_connection(snapshot, previous_local_ready)
        self._sync_steering_state()

    def _sync_terminal_connection(self, snapshot: ReadinessSnapshot, previous_local_ready: bool) -> None:
        if snapshot.local_ready and not previous_local_ready:
            detail = f"Kali sandbox '{snapshot.kali_container}' is ready."
            self.terminal.set_connected(True, detail)
            self._attach_terminal_container()
        elif not snapshot.local_ready and previous_local_ready:
            self.terminal.set_connected(False, "Kali sandbox is no longer available.")

    def _attach_terminal_container(self) -> None:
        controller = self.controller
        container_getter = getattr(controller, "kali_container", None) if controller is not None else None
        if not callable(container_getter):
            return

        async def _attach() -> None:
            try:
                container = await asyncio.to_thread(container_getter)
            except Exception as exc:  # pragma: no cover - defensive
                self.terminal.set_connected(False, f"Could not attach the Kali container: {exc}")
                return
            if container is None:
                self.terminal.set_connected(False, "Kali container is not available.")
            else:
                self.terminal.set_container(container)

        self._spawn(_attach(), context="attach terminal container")

    def _sync_steering_state(self) -> None:
        if not self._running:
            self.bottom_bar.set_steering_enabled(False, "Start an investigation to steer it\u2026")
            return
        if self._current_engine == "ai":
            self.bottom_bar.set_steering_enabled(True, "Send instructions to steer the AI agent\u2026")
        else:
            self.bottom_bar.set_steering_enabled(
                False, "The local recon engine runs a fixed playbook and does not accept steering."
            )

    # -- page switching --------------------------------------------------------------------------------
    def switch_to_tab(self, key: str) -> None:
        """Programmatically switch pages (also used by the headless render tool)."""
        index = _PAGE_INDEX.get(str(key).strip().lower())
        if index is None:
            raise ValueError("Unknown page {!r}; expected one of {}".format(key, ", ".join(_PAGES)))
        page = _PAGES[index]
        self.sidebar.select_tab(page)
        self.stack.setCurrentIndex(index)
        if page == "Sessions":
            self._on_sessions_shown()
        elif page == "Models":
            refresh = getattr(self.models_panel, "refresh", None)
            if callable(refresh):
                with contextlib.suppress(Exception):
                    refresh()
        elif page == "Settings":
            refresh = getattr(self.settings_panel, "refresh", None)
            if callable(refresh):
                with contextlib.suppress(Exception):
                    refresh()

    def _on_sidebar_tab(self, key: str) -> None:
        self.switch_to_tab(key)

    def _on_sessions_shown(self) -> None:
        panel = self.sessions_panel
        load = getattr(panel, "load", None)
        if callable(load) and not self._sessions_panel_loaded and self.controller is not None:
            self._sessions_panel_loaded = True
            with contextlib.suppress(Exception):
                load(self.controller)
        refresh = getattr(panel, "refresh", None)
        if callable(refresh):
            with contextlib.suppress(Exception):
                refresh()
        self._refresh_sessions()

    # -- target prefill / scope hint -------------------------------------------------------------------
    def _prefill_from_settings(self) -> None:
        """Prefill the investigation card from the persisted default target/mode, when it is valid."""
        raw_target, mode = _settings_prefill()
        self.investigation.set_mode(mode)
        self._current_mode = mode
        raw = str(raw_target or "").strip()
        if not raw:
            return
        cleaned = raw
        validator = getattr(self.controller, "validate_target", None)
        if callable(validator):
            with contextlib.suppress(Exception):
                cleaned_value, error = validator(raw)
                if error or not cleaned_value:
                    self.investigation.set_scope_hint(
                        f"Saved default target is invalid: {error or raw}", severity="warning"
                    )
                    return
                cleaned = str(cleaned_value)
        self.investigation.set_target(cleaned)
        self._current_target = cleaned
        self.top_bar.set_target_mode(cleaned, mode)
        self._update_target_hint(cleaned)

    def _on_target_changed(self, target: str) -> None:
        self.top_bar.set_target_mode(target, self._current_mode)
        self._update_target_hint(target)

    def _update_target_hint(self, target: str) -> None:
        """Validate the target and warn (subtly) when it falls outside the authorized scope."""
        if self.controller is None:
            self.investigation.set_scope_hint("")
            return
        raw = str(target or "").strip()
        if not raw:
            self.investigation.set_scope_hint("")
            return
        validator = getattr(self.controller, "validate_target", None)
        if not callable(validator):
            self.investigation.set_scope_hint("")
            return
        try:
            cleaned, error = validator(raw)
        except Exception:
            self.investigation.set_scope_hint("")
            return
        if error or not cleaned:
            self.investigation.set_scope_hint(
                error or "Enter a valid target domain or IPv4 address.", severity="error"
            )
            return
        authorized = _settings_authorized_scope()
        if authorized is None:
            self.investigation.set_scope_hint("")
            return
        checker = getattr(self.controller, "target_in_authorized_scope", None)
        if not callable(checker):
            self.investigation.set_scope_hint("")
            return
        try:
            in_scope = bool(checker(str(cleaned), list(authorized)))
        except Exception:
            in_scope = True
        if in_scope:
            self.investigation.set_scope_hint("")
        else:
            self.investigation.set_scope_hint(
                f"'{cleaned}' is outside the authorized scope \u2014 add it under Targets before starting.",
                severity="warning",
            )

    # -- controller event slots ------------------------------------------------------------------------
    def _on_agent_event(self, event: AgentEvent) -> None:
        row = viewmodels.feed_row(event)
        if row is not None:
            self.investigation.add_feed_row(row)
        if event.type is EventType.STATE_CHANGED:
            state = _coerce_agent_state(event.payload.get("state"))
            if state is not None:
                self._apply_state(state, feed=False)
        elif event.type is EventType.TOOL_STARTED:
            self.investigation.set_phase("Running {}".format(str(event.payload.get("tool", "tool"))))
        elif event.type is EventType.TOOL_COMPLETED:
            self._tool_count += 1
            self.investigation.set_counters(tools=self._tool_count, findings=len(self._findings))
            self._refresh_progress_phase()
            self._refresh_current_tree()
        elif event.type is EventType.FINDING:
            self._refresh_findings_from_controller()
            self._refresh_progress_phase()
        elif event.type is EventType.OBSERVATION:
            self._refresh_current_tree()

    def _on_tool_output(self, tool_name: str, argv: Any, output: str) -> None:
        if tool_name == "[SYSTEM]":
            self.terminal.append_system(output)
        else:
            arguments = list(argv) if isinstance(argv, (list, tuple)) else [str(argv)]
            self.terminal.append_tool_output(tool_name, arguments, output)

    def _on_state_change(self, state: Any) -> None:
        agent_state = _coerce_agent_state(state)
        if agent_state is None:
            return
        self._apply_state(agent_state)

    def _on_completed(self, outcome: Any, *extra: Any) -> None:
        payload = outcome
        if not hasattr(payload, "findings") and extra:
            payload = extra[0]
        self._apply_outcome(payload)

    def _on_error(self, error: Any) -> None:
        exc = error if isinstance(error, BaseException) else RuntimeError(str(error))
        self._error_reported = exc
        self._report_problem(str(exc))
        self._apply_state(AgentState.ERROR_HANDLING)

    # -- run lifecycle ---------------------------------------------------------------------------------
    def _on_start_requested(self) -> None:
        if self._running:
            return
        controller = self.controller
        if controller is None:
            self._report_problem("No investigation controller is attached. Launch the desktop app to scan.")
            return
        raw_target = self.investigation.target()
        try:
            validator = getattr(controller, "validate_target", None)
            if callable(validator):
                cleaned, error = validator(raw_target)
            else:  # pragma: no cover - v1 controller always validates
                cleaned, error = raw_target.strip(), None if raw_target.strip() else "Enter a target."
        except Exception as exc:
            self._report_problem(f"Target validation failed: {exc}")
            return
        if error or not cleaned:
            self._report_problem(error or "Enter a valid target domain or IPv4 address.")
            return
        self._update_target_hint(str(cleaned))
        authorized = _settings_authorized_scope()
        scope_checker = getattr(controller, "target_in_authorized_scope", None)
        if (
            authorized is not None
            and callable(scope_checker)
            and not bool(scope_checker(str(cleaned), list(authorized)))
        ):
            self._report_problem(
                f"'{cleaned}' is outside the authorized scope. Add it under Targets before starting."
            )
            return

        engine = self.investigation.engine()
        mode = self.investigation.mode()
        snapshot = self._readiness
        block = viewmodels.start_block_reason(
            available=snapshot.available,
            checking=snapshot.checking,
            docker_running=snapshot.docker_running,
            gate_certified=snapshot.gate_certified,
            kali_container=snapshot.kali_container,
            ai_ready=snapshot.ai_ready,
            engine=engine,
        )
        if block:
            self._report_problem(block)
            return

        provider = ""
        model = ""
        api_key: str | None = None
        if engine == "ai":
            provider, model, api_key = self._ai_credentials(snapshot)
            if not api_key:
                self._report_problem(
                    "No API key is configured for {}. Add one in Settings or use the Local recon engine.".format(
                        provider or "the selected provider"
                    )
                )
                return

        self._current_target = str(cleaned)
        self._current_mode = mode
        self._current_engine = engine
        self._begin_run(str(cleaned))
        self.top_bar.set_target_mode(str(cleaned), mode)
        self.footer.set_engine(engine, provider, model)
        start = getattr(controller, "start", None)
        if not callable(start):
            self._report_problem("The controller cannot start investigations.")
            self._apply_state(AgentState.IDLE)
            return
        try:
            awaitable = start(
                target=str(cleaned),
                mode=mode,
                engine=engine,
                provider=provider,
                model=model,
                api_key=api_key,
            )
        except Exception as exc:
            self._handle_start_failure(exc)
            return
        if not inspect.isawaitable(awaitable):
            self._handle_start_failure(RuntimeError("Controller.start() did not return an awaitable."))
            return
        task = self._schedule(awaitable)
        if task is None:
            self._report_problem(
                "The Qt asyncio event loop is not running, so the investigation cannot start. "
                "Launch the app with run_ui.bat."
            )
            self._apply_state(AgentState.IDLE)
            return
        self._start_task = task

    def _ai_credentials(self, snapshot: ReadinessSnapshot) -> tuple[str, str, str | None]:
        provider = snapshot.provider
        model = snapshot.model
        try:
            from bletiner.ui.settings_manager import get_settings_manager

            manager = get_settings_manager()
            provider = provider or str(getattr(manager.settings, "active_provider", "") or "")
            model = model or str(getattr(manager.settings, "active_model", "") or "")
            key = manager.get_api_key(provider) if provider else ""
        except Exception:
            key = ""
        return provider, model, (str(key) if key else None)

    def _begin_run(self, target: str) -> None:
        self._error_reported = None
        self._reset_run_counters()
        self.investigation.clear_error()
        self.investigation.clear_feed()
        self.investigation.set_target(target)
        self.investigation.set_progress(0, "Starting\u2026")
        self.investigation.set_badges([])
        self.investigation.add_feed_row(
            viewmodels.FeedRow(
                _stamp(),
                f"Starting {viewmodels.engine_label(self._current_engine)} investigation of {target} ({viewmodels.mode_label(self._current_mode)})",
                "\u25b6",
                "info",
            )
        )
        self.terminal.append_dispatch(
            f"investigation start: target={target} engine={self._current_engine} mode={self._current_mode}"
        )
        self._apply_state(AgentState.INITIALIZING)

    def _reset_run_counters(self) -> None:
        self._tool_count = 0
        self._findings = []
        self.investigation.set_counters(tools=0, findings=0)

    def _schedule(self, awaitable: Any) -> asyncio.Task[Any] | None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            with contextlib.suppress(Exception):
                awaitable.close()  # type: ignore[union-attr]
            return None
        task = loop.create_task(awaitable)
        task.add_done_callback(self._on_start_task_done)
        return task

    def _on_start_task_done(self, task: asyncio.Task[Any]) -> None:
        self._start_task = None
        if task.cancelled():
            self._apply_state(AgentState.STOPPED)
            return
        error = task.exception()
        if error is not None:
            if error is self._error_reported:
                self._apply_state(AgentState.ERROR_HANDLING)
            else:
                self._handle_start_failure(error)
            return
        outcome = task.result()
        self._apply_outcome(outcome)

    def _handle_start_failure(self, error: BaseException) -> None:
        self._error_reported = error
        self._report_problem(str(error))
        self._apply_state(AgentState.ERROR_HANDLING)

    def _apply_outcome(self, outcome: Any) -> None:
        if outcome is None:
            return
        if outcome is self._last_applied_outcome:
            return
        self._last_applied_outcome = outcome
        findings = list(getattr(outcome, "findings", []) or [])
        if not findings:
            findings = self._controller_findings()
        self._findings = findings
        target = str(getattr(outcome, "target", "") or self._current_target)
        engine = str(getattr(outcome, "engine", "") or self._current_engine)
        status = str(getattr(outcome, "status", "completed") or "completed")
        paths = getattr(outcome, "paths", None)
        reports = getattr(outcome, "reports", None)
        duration = getattr(outcome, "duration_s", None)
        stop_reason = str(getattr(outcome, "stop_reason", "completed") or "completed")
        final_state = (
            AgentState.STOPPED
            if status == "stopped"
            else (AgentState.ERROR_HANDLING if status == "error" else AgentState.COMPLETED)
        )
        self._apply_state(final_state)
        if final_state is AgentState.ERROR_HANDLING and status == "error":
            notes = tuple(str(note) for note in (getattr(outcome, "notes", ()) or ()))
            self._report_problem(notes[-1] if notes else "The investigation failed. See the activity feed.")
        self.investigation.set_badges(findings)
        percent = 100 if final_state is AgentState.COMPLETED else self.investigation.progress.value()
        self.investigation.set_progress(
            percent,
            viewmodels.phase_description(final_state, tools=self._tool_count, findings=len(findings)),
        )
        if paths is not None:
            self._shown_paths = paths
            with contextlib.suppress(Exception):
                self.sessions.set_current(
                    target,
                    paths,
                    engine=engine,
                    status=status,
                    findings_total=len(findings),
                )
        summary = viewmodels.summarize_outcome(
            findings=len(findings), duration_s=duration, stop_reason=stop_reason
        )
        glyph = "\u2713" if status == "completed" else "\u25a0"
        kind = "success" if status == "completed" else "info"
        self.investigation.add_feed_row(viewmodels.FeedRow(_stamp(), summary, glyph, kind))
        self.terminal.append_system(summary)
        if reports:
            report_text = f"Reports written: {reports[0]} \u00b7 {reports[1]}"
            self.investigation.add_feed_row(viewmodels.FeedRow(_stamp(), report_text, "\u2713", "success"))
            self.terminal.append_system(report_text)
        tokens = int(getattr(outcome, "tokens_used", 0) or 0)
        iterations = int(getattr(outcome, "iterations", 0) or 0)
        if iterations:
            usage = f", {viewmodels.format_tokens(tokens)} used" if tokens else ""
            self.investigation.add_feed_row(
                viewmodels.FeedRow(_stamp(), f"{iterations} iteration(s){usage}", "\u24d8", "info")
            )
        self._refresh_sessions()
        self.refresh_readiness()

    def _refresh_progress_phase(self) -> None:
        phase = viewmodels.phase_description(
            self._state, tools=self._tool_count, findings=len(self._findings)
        )
        self.investigation.set_phase(phase)

    def _refresh_findings_from_controller(self) -> None:
        findings = self._controller_findings()
        self._findings = findings
        self.investigation.set_badges(findings)
        self.investigation.set_counters(tools=self._tool_count, findings=len(findings))

    def _controller_findings(self) -> list[Finding]:
        controller = self.controller
        if controller is None:
            return []
        getter = getattr(controller, "current_findings", None)
        value: Any = getter() if callable(getter) else getter
        if isinstance(value, (list, tuple)):
            return [finding for finding in value if isinstance(finding, Finding)]
        return []

    def _apply_state(self, state: AgentState, *, feed: bool = True) -> None:
        previous = self._state
        self._state = state
        self._running = viewmodels.is_running_state(state)
        self.top_bar.set_state(state)
        self.bottom_bar.set_state(viewmodels.status_label(state), viewmodels.state_pill_variant(state))
        self.bottom_bar.set_paused(state is AgentState.PAUSED)
        self.investigation.apply_state(state)
        self._refresh_progress_phase()
        self._sync_steering_state()
        if (
            feed
            and previous is not state
            and state
            in (
                AgentState.PAUSED,
                AgentState.RESUMING,
                AgentState.STOPPED,
                AgentState.ERROR_HANDLING,
            )
        ):
            glyph = (
                "\u2016"
                if state is AgentState.PAUSED
                else ("\u25b6" if state is AgentState.RESUMING else "\u25a0")
            )
            kind = "steering" if state in (AgentState.PAUSED, AgentState.RESUMING) else "info"
            self.investigation.add_feed_row(
                viewmodels.FeedRow(_stamp(), viewmodels.status_label(state), glyph, kind)
            )
        if state is AgentState.PAUSED:
            self.bottom_bar.set_steering_enabled(False, "Investigation paused \u2014 resume to steer.")
        elif self._running:
            self._sync_steering_state()

    # -- operator controls -----------------------------------------------------------------------------
    def _on_pause_clicked(self) -> None:
        controller = self.controller
        if controller is None or not self._running:
            return
        try:
            if bool(getattr(controller, "is_paused", False)):
                resume = getattr(controller, "resume", None)
                if callable(resume):
                    resume()
            else:
                pause = getattr(controller, "pause", None)
                if callable(pause):
                    pause()
        except Exception as exc:
            self._report_problem(f"Pause/resume failed: {exc}")

    def _on_stop_clicked(self) -> None:
        controller = self.controller
        if controller is None or not self._running:
            return
        if not self._confirm_stop():
            return
        self.investigation.add_feed_row(
            viewmodels.FeedRow(_stamp(), "Stop requested by operator", "\u25a0", "info")
        )
        stop = getattr(controller, "stop", None)
        if callable(stop):
            try:
                stop()
            except Exception as exc:
                self._report_problem(f"Stop failed: {exc}")

    def _confirm_stop(self) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle("Stop investigation")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText("Stop the running investigation?")
        box.setInformativeText(
            "Real tools are aborted between steps. Partial evidence and findings stay in the evidence folder."
        )
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        return box.exec() == QMessageBox.StandardButton.Yes

    def _on_steering_submitted(self, text: str) -> None:
        message = text.strip()
        if not message:
            return
        controller = self.controller
        if controller is None or not self._running:
            self._report_problem("No investigation is running to steer.")
            return
        submit = getattr(controller, "submit_steering", None)
        if not callable(submit):
            self._report_problem("This controller does not support steering.")
            return
        try:
            result = submit(message)
        except Exception as exc:
            self._report_problem(f"Steering failed: {exc}")
            return
        accepted = bool(getattr(result, "accepted", False))
        if accepted:
            self.bottom_bar.clear_steering()
            self.investigation.add_feed_row(
                viewmodels.FeedRow(_stamp(), f"Steering queued: {redact(message)}", "\u27a4", "steering")
            )
            self.terminal.append_dispatch(f"operator steering: {message}")
        else:
            reason = str(getattr(result, "reason", "") or "steering rejected")
            self._report_problem(f"Steering rejected: {reason}")
            self.terminal.append_system(f"Steering rejected: {reason}")

    def _on_terminal_command_entered(self, command: str) -> None:
        text = command.strip()
        if text:
            self.investigation.add_feed_row(
                viewmodels.FeedRow(_stamp(), f"Operator command: {redact(text)}", "\u25b8", "info")
            )

    # -- sessions / findings ---------------------------------------------------------------------------
    def _refresh_sessions(self) -> None:
        controller = self.controller
        if controller is None:
            return
        sessions = getattr(controller, "sessions", None)
        if not callable(sessions):
            return

        async def _load() -> None:
            try:
                summaries = await sessions(limit=50)
            except Exception as exc:
                self._report_problem(f"Could not load session history: {exc}")
                return
            self._previous_sessions = list(summaries)
            with contextlib.suppress(Exception):
                self.sessions.set_previous(list(summaries))

        self._spawn(_load(), context="load sessions")

    def _open_session_findings(self, investigation_id: str) -> None:
        identifier = str(investigation_id or "").strip()
        if not identifier:
            return
        controller = self.controller
        loader = getattr(controller, "findings_for", None) if controller is not None else None
        if not callable(loader):
            self._report_problem("Session findings are unavailable from this controller.")
            return

        async def _load() -> None:
            try:
                findings = await loader(identifier)
            except Exception as exc:
                self._report_problem(f"Could not load session findings: {exc}")
                return
            self._show_findings_dialog(list(findings), None, label=f"session {identifier}")

        self._spawn(_load(), context="load session findings")

    def _open_findings(self, filter_state: str) -> None:
        findings = self._controller_findings() or list(self._findings)
        self._show_findings_dialog(findings, filter_state, label="current investigation")

    def _show_findings_dialog(
        self, findings: list[Finding], filter_state: str | None, *, label: str = ""
    ) -> None:
        dialog_class = _findings_dialog_class()
        if dialog_class is None:
            self._report_problem("The findings dialog module is unavailable.")
            return
        try:
            dialog = dialog_class(findings, filter_state=filter_state, parent=self)
        except TypeError:
            dialog = dialog_class(findings, parent=self)
        title = "Findings"
        if label:
            title += " \u2014 " + label
        with contextlib.suppress(Exception):
            dialog.setWindowTitle(title)
        try:
            dialog.exec()
        except Exception as exc:  # pragma: no cover - defensive
            self._report_problem(f"Could not open the findings dialog: {exc}")

    def _refresh_current_tree(self) -> None:
        controller = self.controller
        paths = getattr(controller, "current_paths", None) if controller is not None else None
        if paths is None:
            return
        self._shown_paths = paths
        with contextlib.suppress(Exception):
            self.sessions.set_current(
                self._current_target or "current investigation",
                paths,
                engine=self._current_engine,
                status="running" if self._running else "completed",
                findings_total=len(self._findings),
            )
            refresh = getattr(self.sessions, "refresh_current_tree", None)
            if callable(refresh):
                refresh()

    # -- panels ----------------------------------------------------------------------------------------
    def _on_engine_changed(self, engine: str) -> None:
        self._current_engine = "ai" if str(engine).lower() == "ai" else "local"
        snapshot = self._readiness
        self.footer.set_engine(self._current_engine, snapshot.provider, snapshot.model)
        self._sync_steering_state()
        self.investigation.apply_readiness(snapshot)

    def _on_targets_selected(self, target: str, mode: str) -> None:
        if self._running:
            self._report_problem("An investigation is already running; wait for it to finish first.")
            return
        self.investigation.set_target(str(target or ""))
        self.investigation.set_mode(str(mode or "deep"))
        self._current_target = str(target or "")
        self._current_mode = str(mode or "deep")
        self.top_bar.set_target_mode(self._current_target, self._current_mode)
        self._update_target_hint(self._current_target)
        self.switch_to_tab("Investigation")
        self._on_start_requested()

    def _on_model_changed(self, model_id: str, provider: str) -> None:
        model_name = model_id
        getter = getattr(self.models_panel, "get_active_model_name", None)
        if callable(getter):
            with contextlib.suppress(Exception):
                model_name = str(getter() or model_id)
        self.footer.set_engine(
            self._current_engine,
            str(provider or self._readiness.provider),
            model_name,
        )
        self.investigation.add_feed_row(
            viewmodels.FeedRow(
                _stamp(),
                "AI model selection changed: {} ({})".format(
                    model_name or "unspecified", provider or "unknown"
                ),
                "\u24d8",
                "info",
            )
        )
        self.refresh_readiness()

    def _on_settings_updated(self) -> None:
        self.refresh_readiness()
        refresh = getattr(self.models_panel, "refresh", None)
        if callable(refresh):
            with contextlib.suppress(Exception):
                refresh()
        self.investigation.add_feed_row(
            viewmodels.FeedRow(_stamp(), "Settings saved; readiness re-checked", "\u24d8", "info")
        )

    # -- new investigation -----------------------------------------------------------------------------
    def open_new_investigation_dialog(self) -> None:
        """Open the configuration dialog; falls back to the Targets page when it is unavailable."""
        dialog_class = _new_investigation_dialog_class()
        if dialog_class is None:
            self.investigation.add_feed_row(
                viewmodels.FeedRow(
                    _stamp(),
                    "New-investigation dialog unavailable; opening the Targets page instead",
                    "\u24d8",
                    "info",
                )
            )
            self.switch_to_tab("Targets")
            focus = getattr(self.targets_panel, "focus_target_input", None)
            if callable(focus):
                focus()
            return
        snapshot = self._readiness
        try:
            dialog = _construct_new_investigation_dialog(
                dialog_class,
                parent=self,
                target=self.investigation.target() or self._current_target,
                mode=self.investigation.mode(),
                engine=self.investigation.engine(),
                provider=snapshot.provider,
                model=snapshot.model,
            )
        except Exception as exc:
            self._report_problem(f"Could not open the new investigation dialog: {exc}")
            self.switch_to_tab("Targets")
            return
        try:
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
        except Exception as exc:  # pragma: no cover - defensive
            self._report_problem(f"New investigation dialog failed: {exc}")
            return
        if not accepted:
            return
        target = str(_dialog_value(dialog, "target") or "").strip()
        mode = str(_dialog_value(dialog, "mode") or "deep")
        engine = str(_dialog_value(dialog, "engine") or "local")
        provider = str(_dialog_value(dialog, "provider") or "")
        model = str(_dialog_value(dialog, "model") or "")
        requested_engine = "ai" if str(engine).lower() == "ai" else "local"
        self.investigation.set_target(target)
        self.investigation.set_mode(mode)
        self.investigation.set_engine(requested_engine)
        if requested_engine == "ai" and (provider or model):
            self.footer.set_engine(requested_engine, provider, model)
        self._current_target = target
        self._current_mode = mode
        self.top_bar.set_target_mode(target, mode)
        self.switch_to_tab("Investigation")
        if requested_engine == "ai" and self.investigation.engine() != "ai":
            snapshot = self._readiness
            reason = viewmodels.ai_engine_block_reason(
                docker_running=snapshot.docker_running,
                gate_certified=snapshot.gate_certified,
                kali_container=snapshot.kali_container,
                ai_ready=snapshot.ai_ready,
            )
            self._report_problem(f"The AI engine is unavailable: {reason or 'readiness is incomplete'}.")
            return
        if target:
            self._on_start_requested()

    # -- misc actions ----------------------------------------------------------------------------------
    def _toggle_terminal_expand(self) -> None:
        self._terminal_expanded = not self._terminal_expanded
        if self._terminal_expanded:
            self._saved_splitter_sizes = self.splitter.sizes()
            self.investigation.setVisible(False)
            self.sessions.setVisible(False)
            total = max(1, sum(self._saved_splitter_sizes))
            self.splitter.setSizes([0, total, 0])
            self.terminal.focus_terminal()
        else:
            self.investigation.setVisible(True)
            self.sessions.setVisible(True)
            sizes = self._saved_splitter_sizes or [370, 760, 380]
            self.splitter.setSizes(sizes)

    def _focus_steering(self) -> None:
        if self.bottom_bar.steering_input.isEnabled():
            self.bottom_bar.steering_input.setFocus()
        else:
            self.investigation.add_feed_row(
                viewmodels.FeedRow(
                    _stamp(),
                    "Steering is available only while an AI-engine investigation is running",
                    "\u24d8",
                    "info",
                )
            )

    def _focus_terminal(self) -> None:
        focus = getattr(self.terminal, "focus_terminal", None)
        if callable(focus):
            focus()

    def _on_refresh(self) -> None:
        self.refresh_readiness()
        for panel in (self.models_panel, self.settings_panel, self.sessions_panel):
            refresh = getattr(panel, "refresh", None)
            if callable(refresh):
                with contextlib.suppress(Exception):
                    refresh()
        self._refresh_sessions()
        self.investigation.add_feed_row(viewmodels.FeedRow(_stamp(), "Status refreshed", "\u27f3", "info"))

    def _on_escape(self) -> None:
        modal = QApplication.activeModalWidget()
        if isinstance(modal, QDialog):
            modal.reject()
            return
        if self._terminal_expanded:
            self._toggle_terminal_expand()
            return
        self.setFocus()

    # -- helpers ---------------------------------------------------------------------------------------
    def _report_problem(self, message: str, error: BaseException | None = None) -> None:
        """Surface an error visibly: inline banner + activity feed + terminal (never a silent no-op)."""
        text = str(message or "Unknown error")
        if error is not None:
            self._error_reported = error
        self.investigation.show_error(text)
        self.investigation.add_feed_row(viewmodels.FeedRow(_stamp(), redact(text), "\u2715", "error"))
        self.terminal.append_system("[error] " + text)

    def _spawn(self, awaitable: Any, *, context: str) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Headless render path: no Qt asyncio loop exists, so run this short DB read synchronously.
            try:
                asyncio.run(awaitable)
            except Exception as exc:
                self._report_problem(f"{context} failed: {exc}")
            return
        task = loop.create_task(awaitable)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        task.add_done_callback(lambda finished: self._on_background_done(finished, context))

    def _on_background_done(self, task: asyncio.Task[Any], context: str) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self._report_problem(f"{context} failed: {error}")

    def _initial_async_loads(self) -> None:
        if self.controller is None:
            return
        self._refresh_sessions()
        panel = self.sessions_panel
        load = getattr(panel, "load", None)
        if callable(load):
            self._sessions_panel_loaded = True
            with contextlib.suppress(Exception):
                load(self.controller)

    # -- Qt overrides ----------------------------------------------------------------------------------
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._dark_titlebar_applied:
            self._dark_titlebar_applied = True
            theme.apply_dark_titlebar(self)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._readiness_timer.stop()
        super().closeEvent(event)


# ---------------------------------------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------------------------------------
def _stamp() -> str:
    return datetime.now(UTC).strftime("%H:%M:%S")


def _coerce_agent_state(value: Any) -> AgentState | None:
    if isinstance(value, AgentState):
        return value
    try:
        return AgentState(str(value))
    except ValueError:
        return None


def _dialog_value(dialog: Any, name: str) -> Any:
    """Read a dialog field that may be a method (v1 contract) or a property (legacy scaffold)."""
    value = getattr(dialog, name, None)
    if callable(value):
        with contextlib.suppress(Exception):
            return value()
        return None
    return value


def _construct_new_investigation_dialog(
    dialog_class: Any,
    *,
    parent: QWidget,
    target: str,
    mode: str,
    engine: str,
    provider: str,
    model: str,
) -> Any:
    """Construct the dialog through the INTERFACES signature, tolerating the transitional scaffold."""
    try:
        return dialog_class(
            target=target,
            mode=mode,
            engine=engine,
            provider=provider,
            model=model,
            parent=parent,
        )
    except TypeError:
        return dialog_class(parent=parent, initial_target=target, initial_mode=mode)


def _findings_dialog_class() -> Any:
    try:
        from bletiner.ui.findings_dialog import FindingsDialog

        return FindingsDialog
    except ImportError:
        with contextlib.suppress(ImportError):
            from bletiner.ui.sessions_card import FindingsDialog as LegacyFindingsDialog

            return LegacyFindingsDialog
    return None


def _settings_prefill() -> tuple[str, str]:
    """Persisted default target/mode from the settings manager (top-level helper: easy to test)."""
    try:
        from bletiner.ui.settings_manager import get_settings_manager

        settings = get_settings_manager().settings
    except Exception:
        return "", "deep"
    return (
        str(getattr(settings, "default_target", "") or ""),
        str(getattr(settings, "default_mode", "deep") or "deep"),
    )


def _settings_authorized_scope() -> list[str] | None:
    """Authorized scope list, or None when it cannot be read (scope checks are then skipped)."""
    try:
        from bletiner.ui.settings_manager import get_settings_manager

        scope = getattr(get_settings_manager().settings, "authorized_scope", None)
    except Exception:
        return None
    if not isinstance(scope, list):
        return None
    return [str(entry) for entry in scope]


def _new_investigation_dialog_class() -> Any:
    try:
        from bletiner.ui.dialogs import NewInvestigationDialog

        return NewInvestigationDialog
    except ImportError:
        return None


# ---------------------------------------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------------------------------------
def build_main_window(controller: Any = None) -> MainWindow:
    """Construct the main window; works under QT_QPA_PLATFORM=offscreen and with controller=None."""
    return MainWindow(controller=controller)


def launch() -> int:
    """Run the desktop workspace on a qasync event loop."""
    import asyncio

    import qasync

    from bletiner.ui.controller import InvestigationController

    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Bletiner AI")
    app.setApplicationDisplayName("Bletiner AI")
    theme.apply_dark_theme(app)
    controller = InvestigationController()
    window = build_main_window(controller=controller)
    window.show()
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    try:
        with loop:
            loop.run_forever()
    finally:
        with contextlib.suppress(Exception):
            controller.shutdown()
    return 0
