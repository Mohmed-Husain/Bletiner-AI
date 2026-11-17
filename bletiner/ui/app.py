"""PySide6 desktop bootstrap (DOC-05, DOC-14 Phase 5).

Owns the Qt + asyncio (qasync) lifecycle and graceful shutdown. The widgets live in
bletiner.ui.desktop; the async bridge lives in bletiner.ui.controller.
"""

from __future__ import annotations

import asyncio
import logging
import sys

logger = logging.getLogger(__name__)


def qt_available() -> bool:
    try:
        import PySide6  # noqa: F401
    except ImportError:
        return False
    return True


def run() -> int:  # pragma: no cover - requires a display + PySide6
    """Launch the desktop workspace. Returns the process exit code."""
    try:
        import qasync
        from PySide6.QtWidgets import QApplication
    except ImportError as exc:
        sys.stderr.write(f'PySide6/qasync are not installed. Run: pip install -e ".[ui]"\n({exc})\n')
        return 2

    from bletiner.ui import theme
    from bletiner.ui.controller import InvestigationController
    from bletiner.ui.desktop import MainWindow

    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Bletiner AI")
    theme.apply_dark_theme(app)

    controller = InvestigationController()
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    window = MainWindow(controller=controller)
    window.show()

    exit_code = 0
    try:
        with loop:
            loop.run_forever()
            loop.run_until_complete(controller.aclose())
    except KeyboardInterrupt:
        exit_code = 130
    except Exception:
        logger.exception("fatal UI error")
        exit_code = 1
    finally:
        controller.shutdown()
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())
