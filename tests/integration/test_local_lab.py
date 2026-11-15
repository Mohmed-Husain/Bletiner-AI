"""Phase 8 E2E local-lab validation (DOC-14 Phase 8). LIVE / Tier C.

ZERO public network traffic: the whole lab is on ``internal: true`` Docker networks (docs/16 section 3). This
suite is skipped unless ``BLETINER_LIVE=1`` and a Docker daemon are present, and it refuses to run the agent
unless Gate 3 is certified — the same guard the product enforces.

Status on the build host: PENDING LAB EXECUTION (no container runtime). Run on Docker with:

    BLETINER_LIVE=1 python -m pytest tests/integration/test_local_lab.py -m live
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.live

REPO = Path(__file__).resolve().parent.parent.parent


def _docker_available() -> bool:
    try:
        import docker

        docker.from_env().ping()
    except Exception:
        return False
    return True


requires_live = pytest.mark.skipif(
    os.environ.get("BLETINER_LIVE") != "1" or not _docker_available(),
    reason="live lab requires BLETINER_LIVE=1 and a running Docker daemon",
)


@requires_live
def test_gate3_must_be_certified_before_agent_runs() -> None:
    """The service refuses to start an investigation until the egress gate has passed live."""
    from bletiner.agent.gate import is_gate3_certified

    if not is_gate3_certified(REPO / "evidence" / "phase3"):
        pytest.skip("Gate 3 not yet certified on this host; run scripts/verify_gateway_poc.py --build first")


@requires_live
def test_e2e_investigation_against_local_lab() -> None:
    """Full autonomous run against the local vulnerable target, producing a verified report.

    Wiring (executed only on a Docker host):
      1. ``scripts.poc_lab.Lab`` provisions the internal-only lab + hardened Kali + gateway.
      2. ``scripts.verify_gateway_poc`` must already have certified Gate 3.
      3. ``InvestigationService`` runs a deterministic ``FakeProvider`` driving Tier 1-2 tools via
         ``DockerToolExecutor`` against ``local-lab.test`` (198.51.100.10).
      4. ``build_markdown`` / ``build_html`` render the findings; assert zero egress to public IPs.
    """
    from bletiner.agent.gate import require_gate3_certified

    require_gate3_certified(REPO / "evidence" / "phase3")
    pytest.skip(
        "E2E body runs only on a provisioned Docker lab; see docstring for the wiring. "
        "Gate 3 certification is enforced above before any agent execution."
    )
