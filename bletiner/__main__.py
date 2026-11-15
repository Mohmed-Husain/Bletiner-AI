"""Command-line entry point: ``python -m bletiner``."""

from __future__ import annotations

import argparse
import sys

from bletiner import SPEC_REVISION, __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bletiner",
        description=(
            "Bletiner AI - authorized security investigation workspace. "
            "Phases 0-3 only: the autonomous agent loop is intentionally not implemented until the "
            "live egress gateway gate (scripts/verify_gateway_poc.py) has passed."
        ),
    )
    parser.add_argument("--version", action="store_true", help="print version information and exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.version:
        sys.stdout.write(f"bletiner {__version__} (spec revision {SPEC_REVISION})\n")
        return 0
    build_parser().print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
