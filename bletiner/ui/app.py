"""PySide6 desktop bootstrap (DOC-05, DOC-14 Phase 5).

Thin shell over the headless view-models in ``viewmodels.py``. PySide6/qasync are optional extras imported
lazily, so the rest of the package (and the test suite) runs without a Qt toolchain installed. This module is
exercised live only when launching the desktop app; it is excluded from unit coverage.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bletiner.ui.viewmodels import FeedViewModel, new_terminal_token

if TYPE_CHECKING:  # pragma: no cover
    pass


def qt_available() -> bool:
    try:
        import PySide6  # noqa: F401
    except ImportError:
        return False
    return True


def run() -> int:  # pragma: no cover - requires a display + PySide6
    """Launch the 3-card desktop workspace. Returns the Qt exit code."""
    try:
        import qasync
        from PySide6.QtWidgets import (
            QApplication,
            QHBoxLayout,
            QLabel,
            QListWidget,
            QVBoxLayout,
            QWidget,
        )
    except ImportError as exc:
        raise RuntimeError(
            "PySide6/qasync are not installed. Install the UI extra: pip install -e '.[ui]'"
        ) from exc

    import asyncio

    app = QApplication([])
    window = QWidget()
    window.setWindowTitle("Bletiner AI")
    root = QHBoxLayout(window)

    feed = FeedViewModel()
    feed_widget = QListWidget()
    left = QVBoxLayout()
    left.addWidget(QLabel("INVESTIGATION"))
    left.addWidget(feed_widget)
    center = QVBoxLayout()
    center.addWidget(QLabel(">_ KALI LINUX TERMINAL"))
    right = QVBoxLayout()
    right.addWidget(QLabel("SESSIONS & FILES"))
    for col in (left, center, right):
        holder = QWidget()
        holder.setLayout(col)
        root.addWidget(holder)

    window._bletiner_token = new_terminal_token()
    window._bletiner_feed = feed
    window.resize(1280, 800)
    window.show()

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    with loop:
        return int(loop.run_forever() or 0)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())
