"""Phase 0 baseline: CLI entry point resolves --help and --version."""

from __future__ import annotations

import subprocess
import sys

import pytest

from bletiner import __version__
from bletiner.__main__ import main


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == 0
    assert __version__ in capsys.readouterr().out


def test_help_via_module() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "bletiner", "--help"], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0
    assert "agent loop is intentionally not implemented" in proc.stdout
