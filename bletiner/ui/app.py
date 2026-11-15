"""PySide6 desktop bootstrap (DOC-05, DOC-14 Phase 5).

Thin entry point. The Qt widgets live in ``bletiner.ui.desktop`` and are imported lazily, so the rest of the
package (and the test suite) runs without a Qt toolchain. The headless, unit-tested view-models in
``viewmodels.py`` supply all presentation logic.
"""

from __future__ import annotations


def qt_available() -> bool:
    try:
        import PySide6  # noqa: F401
    except ImportError:
        return False
    return True


def run() -> int:  # pragma: no cover - requires a display + PySide6
    """Launch the full 3-card desktop workspace. Returns the Qt exit code."""
    try:
        from bletiner.ui.desktop import launch
    except ImportError as exc:
        raise RuntimeError(
            "PySide6/qasync are not installed. Install the UI extra: pip install -e '.[ui]'"
        ) from exc
    return launch()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())
