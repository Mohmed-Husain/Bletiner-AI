"""Bletiner AI.

This package root must stay dependency-free: the gateway image copies ``bletiner/__init__.py`` and
``bletiner/network/*`` runtime modules without installing the desktop dependencies.
"""

from __future__ import annotations

__version__ = "0.1.0"
SPEC_REVISION = "3.1 (reconciled, see docs/16_REV31_RECONCILIATION.md)"
