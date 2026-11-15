"""Deterministic finding prioritization (DOC-08 section 5.1).

Implements the bounded-product model ``P = 100 * (Ws * Wc * Iv * Sr * Cd)`` with a steering override
multiplier. docs/16 R-30: this contradicts ADR-019's weighted-sum model; DOC-08 section 5.1 is used as
authoritative because it defines every factor table and matches the 0-100 ``priority_score`` DB column.
Weights are configurable but never modified by the LLM (ADR-019 consequence).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from bletiner.models.entities import Confidence, Severity

SEVERITY_WEIGHT: dict[Severity, float] = {
    Severity.CRITICAL: 1.00,
    Severity.HIGH: 0.75,
    Severity.MEDIUM: 0.50,
    Severity.LOW: 0.25,
    Severity.INFO: 0.10,
}
CONFIDENCE_WEIGHT: dict[Confidence, float] = {
    Confidence.CONFIRMED: 1.00,
    Confidence.HIGH: 0.85,
    Confidence.MEDIUM: 0.60,
    Confidence.LOW: 0.35,
}
STEERING_MULTIPLIER = 2.0
NO_DISCARD_THRESHOLD = 15.0  # findings below this are retained, never dropped (DOC-08 section 5.2)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


@dataclass(frozen=True)
class PriorityFactors:
    severity: Severity
    confidence: Confidence
    investigation_value: float = 0.5  # Iv in [0.2, 1.0]
    scope_relevance: float = 1.0  # Sr in [0.1, 1.0]
    cost_discount: float = 1.0  # Cd in [0.5, 1.0]
    steering_boost: bool = False

    def components(self) -> dict[str, float]:
        return {
            "Ws": SEVERITY_WEIGHT[self.severity],
            "Wc": CONFIDENCE_WEIGHT[self.confidence],
            "Iv": _clamp(self.investigation_value, 0.2, 1.0),
            "Sr": _clamp(self.scope_relevance, 0.1, 1.0),
            "Cd": _clamp(self.cost_discount, 0.5, 1.0),
        }


@dataclass(frozen=True)
class PriorityScore:
    value: float
    components: dict[str, float] = field(default_factory=dict)
    steering_applied: bool = False

    @property
    def retained_only(self) -> bool:
        """True when the score is below the no-discard threshold (kept in backlog, not scheduled eagerly)."""
        return self.value < NO_DISCARD_THRESHOLD


def compute_priority(factors: PriorityFactors) -> PriorityScore:
    comp = factors.components()
    product = comp["Ws"] * comp["Wc"] * comp["Iv"] * comp["Sr"] * comp["Cd"]
    score = 100.0 * product
    if factors.steering_boost:
        score *= STEERING_MULTIPLIER
    return PriorityScore(
        value=round(_clamp(score, 0.0, 100.0), 2),
        components={k: round(v, 3) for k, v in comp.items()},
        steering_applied=factors.steering_boost,
    )
