r"""Headless UI renderer for visual review (Lead-owned).

Renders the app to PNG files so reviewers can inspect real screenshots without a display.
Usage: .venv Scripts\python.exe scripts\render_ui.py [--width 1672 --height 941]
"""

import argparse
import contextlib
import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")


def pump(app, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--width", type=int, default=1672)
    parser.add_argument("--height", type=int, default=941)
    parser.add_argument("--no-controller", action="store_true")
    parser.add_argument("--prefix", default="")
    args = parser.parse_args()

    from PySide6.QtWidgets import QApplication

    from bletiner.ui import theme
    from bletiner.ui.desktop import MainWindow

    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Bletiner AI")
    try:
        theme.apply_dark_theme(app)
    except Exception:
        traceback.print_exc()

    controller = None
    if not args.no_controller:
        try:
            from bletiner.ui.controller import InvestigationController

            controller = InvestigationController()
        except Exception:
            traceback.print_exc()
            controller = None

    win = MainWindow(controller=controller)
    win.resize(args.width, args.height)
    win.show()
    pump(app, 0.6)

    # Let the background readiness probe + live terminal connect before capturing.
    deadline = time.monotonic() + 6.0
    while controller is not None and time.monotonic() < deadline:
        try:
            if controller.readiness().probe_complete:
                break
        except Exception:
            break
        pump(app, 0.15)
    pump(app, 2.2)

    out = ROOT / ".handoff" / "shots"
    out.mkdir(parents=True, exist_ok=True)
    tabs = ["Investigation", "Sessions", "Targets", "Models", "Settings"]
    saved = []
    for tab in tabs:
        try:
            switch = getattr(win, "switch_to_tab", None)
            if switch is not None:
                switch(tab)
            pump(app, 0.45)
            path = out / f"{args.prefix}{tab.lower()}.png"
            win.grab().save(str(path))
            saved.append(str(path))
        except Exception:
            traceback.print_exc()

    # Return to the main deck for a final capture after the pages warmed up.
    with contextlib.suppress(Exception):
        switch = getattr(win, "switch_to_tab", None)
        if switch is not None:
            switch("Investigation")
        pump(app, 0.4)
        win.grab().save(str(out / f"{args.prefix}investigation.png"))

    win.close()
    if controller is not None:
        with contextlib.suppress(Exception):
            import asyncio

            loop = asyncio.new_event_loop()
            loop.run_until_complete(controller.aclose())
            loop.close()
    print("\n".join(saved))
    return 0 if saved else 1


if __name__ == "__main__":
    raise SystemExit(main())
