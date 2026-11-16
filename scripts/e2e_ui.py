"""End-to-end UI verification: real local investigation driven through the real MainWindow (qasync loop)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

SHOTS = ROOT / ".handoff" / "shots"  # gitignored screenshot output
SHOTS.mkdir(parents=True, exist_ok=True)
with contextlib.suppress(Exception):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

results: dict[str, object] = {}
failures: list[str] = []


def log(message: str) -> None:
    print("[e2e]", message, flush=True)


def check(name: str, condition: bool, detail: object = "") -> None:
    results[name] = {"pass": bool(condition), "detail": str(detail)[:300]}
    if not condition:
        failures.append(name)
    log(f"{'PASS' if condition else 'FAIL'} {name} ({detail})")


def lab_target_ip() -> str:
    out = subprocess.run(
        [
            "docker",
            "inspect",
            "bletiner-smoke-target",
            "--format",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    ip = out.stdout.strip()
    if out.returncode != 0 or not ip:
        raise SystemExit("lab target container not running: " + out.stderr.strip())
    return ip


async def wait_until(predicate, timeout_s: float, interval: float = 0.25) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            return False
        await asyncio.sleep(interval)
    return False


async def main() -> int:
    from PySide6.QtWidgets import QApplication

    from bletiner.ui import theme
    from bletiner.ui.controller import InvestigationController
    from bletiner.ui.desktop import MainWindow
    from bletiner.ui.findings_dialog import FindingsDialog
    from bletiner.ui.settings_manager import get_settings_manager

    ip = lab_target_ip()
    log(f"lab target ip: {ip}")
    manager = get_settings_manager()
    original_scope = list(manager.settings.authorized_scope)
    if ip not in manager.settings.authorized_scope:
        manager.settings.authorized_scope.append(ip)
        manager.save(manager.settings)

    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Bletiner AI")
    theme.apply_dark_theme(app)
    controller = InvestigationController(monitor_readiness=True)
    win = MainWindow(controller=controller)
    win.resize(1672, 941)
    win.show()
    try:
        log("waiting for readiness")
        await wait_until(
            lambda: controller.readiness().probe_complete and controller.readiness().local_ready, 25
        )
        win.refresh_readiness()
        await wait_until(lambda: win._readiness.probe_complete and win._readiness.local_ready, 10)
        ready = controller.readiness()
        check("readiness_probe_completes", ready.probe_complete)
        check("local_engine_ready", ready.local_ready, ready.kali_container)
        check(
            "readiness_ui_matches",
            win._readiness.probe_complete and win._readiness.local_ready,
            win._readiness.kali_container,
        )

        log("waiting for PTY")
        await wait_until(lambda: bool(getattr(win.terminal, "_pty_connected", False)), 20)
        check("pty_connected", bool(getattr(win.terminal, "_pty_connected", False)))

        win.investigation.target_edit.setText(ip)
        idx = win.investigation.mode_combo.findData("deep")
        if idx >= 0:
            win.investigation.mode_combo.setCurrentIndex(idx)
        win.investigation.btn_local.setChecked(True)
        check("target_set", win.investigation.target_edit.text() == ip)
        await wait_until(win.investigation.btn_start.isEnabled, 10)
        check("start_button_enabled", win.investigation.btn_start.isEnabled(), f"readiness={win._readiness}")

        log("starting investigation")
        win.investigation.btn_start.click()

        async def _run_phase() -> None:
            for _ in range(720):
                await asyncio.sleep(0.25)
                if not win._running and (
                    controller.last_outcome is not None or win._error_reported is not None
                ):
                    return

        try:
            await asyncio.wait_for(_run_phase(), timeout=170)
        except TimeoutError:
            log("run phase timed out")
        await asyncio.sleep(1.0)

        outcome = controller.last_outcome
        status = getattr(outcome, "status", None) if outcome else None
        check(
            "run_completed",
            outcome is not None and status == "completed",
            f"status={status} err={win._error_reported}",
        )
        findings = list(getattr(outcome, "findings", []) or [])
        check("real_findings_present", len(findings) >= 5, len(findings))
        check("ui_findings_count", len(win._findings) == len(findings), len(win._findings))
        check(
            "progress_complete", win.investigation.progress.value() == 100, win.investigation.progress.value()
        )
        check(
            "activity_feed_rows",
            win.investigation.feed_list.count() >= 5,
            win.investigation.feed_list.count(),
        )
        terminal_text = win.terminal.view.toPlainText()
        agent_text = win.terminal.agent_log.toPlainText()
        check(
            "terminal_shell_alive",
            "bletiner@" in terminal_text or "workspace" in terminal_text,
            terminal_text[:80].replace("\n", " "),
        )
        check(
            "terminal_agent_output_real_nmap",
            "Nmap" in agent_text or "nmap" in agent_text,
            agent_text[:100].replace("\n", " "),
        )
        check(
            "reports_written",
            bool(outcome and outcome.reports and all(p.exists() for p in outcome.reports)),
            outcome.reports if outcome else None,
        )
        evidence = Path(outcome.paths.evidence) if outcome else None
        check("evidence_manifest", bool(evidence and (evidence / "manifest.json").exists()))
        check("session_manifest", bool(outcome and (Path(outcome.paths.root) / "session.json").exists()))
        win.grab().save(str(SHOTS / "e2e-investigation-complete.png"))

        log("interactive terminal check")
        win.terminal.cmd_input.setText("echo UI_TERMINAL_OK")
        win.terminal.cmd_input.returnPressed.emit()
        await wait_until(lambda: "UI_TERMINAL_OK" in win.terminal.view.toPlainText(), 10)
        check(
            "interactive_terminal_roundtrip",
            "UI_TERMINAL_OK" in win.terminal.view.toPlainText(),
            win.terminal.view.toPlainText()[-120:].replace("\n", " "),
        )
        win.grab().save(str(SHOTS / "e2e-terminal.png"))

        log("findings dialog")
        dialog = FindingsDialog(findings, filter_state="confirmed", parent=win)
        dialog.show()
        await asyncio.sleep(0.6)
        dialog.grab().save(str(SHOTS / "e2e-findings-dialog.png"))
        dialog.close()

        log("sessions page")
        win.switch_to_tab("Sessions")
        await asyncio.sleep(2.0)
        win.grab().save(str(SHOTS / "e2e-sessions.png"))
        win.switch_to_tab("Investigation")
        await asyncio.sleep(0.8)
        check("sessions_card_previous", win.sessions.previous.count() >= 1, win.sessions.previous.count())
    except Exception:
        traceback.print_exc()
        failures.append("exception")
    finally:
        manager.settings.authorized_scope = original_scope
        manager.save(manager.settings)
        with contextlib.suppress(Exception):
            await controller.aclose()
        win.close()

    print(json.dumps({"results": results, "failures": failures}, indent=2), flush=True)
    return 0 if not failures else 1


def run() -> int:
    import qasync
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    with loop:
        return loop.run_until_complete(main())


if __name__ == "__main__":
    raise SystemExit(run())
