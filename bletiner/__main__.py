"""Command-line entry point: python -m bletiner.

No arguments opens the desktop workspace (when PySide6 is installed).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from bletiner import SPEC_REVISION, __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bletiner",
        description=(
            "Bletiner AI - authorized security investigation workspace. "
            "Local recon engine needs Docker + the Kali sandbox; the AI agent engine additionally needs a "
            "provider API key. Use --check to inspect prerequisites without opening the UI."
        ),
    )
    parser.add_argument("--version", action="store_true", help="print version information and exit")
    parser.add_argument("--ui", action="store_true", help="launch the PySide6 desktop workspace")
    parser.add_argument(
        "--check",
        action="store_true",
        help="print a JSON readiness report (Docker, Kali sandbox, gate, AI configuration) and exit",
    )
    return parser


def _check() -> int:
    from bletiner.ui.controller import InvestigationController

    controller = InvestigationController(monitor_readiness=False)
    readiness = controller._probe_readiness()
    payload = asdict(readiness)
    payload["local_ready"] = readiness.local_ready
    sys.stdout.write(json.dumps(payload, indent=2) + "\n")
    if not readiness.local_ready:
        sys.stderr.write(
            "\nLocal recon needs Docker Desktop running with the bletiner Kali container present.\n"
        )
    if not readiness.ai_ready:
        sys.stderr.write("AI agent mode is not configured (add a provider key in Settings).\n")
    return 0 if readiness.local_ready else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        sys.stdout.write(f"bletiner {__version__} (spec revision {SPEC_REVISION})\n")
        return 0
    if args.check:
        return _check()
    if args.ui or len(sys.argv) == 1:
        from bletiner.ui.app import qt_available

        if not qt_available():
            sys.stderr.write(
                'PySide6 is not installed. Install the UI extra: pip install -e ".[ui]"\n'
                "Or use --check / --version from the command line.\n"
            )
            return 2
        from bletiner.ui.app import run

        return run()
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
