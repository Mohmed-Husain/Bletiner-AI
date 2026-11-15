"""Core immutable domain entities (DOC-14 Phase 1, DOC-07 schema v2 column names)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _uuid36(v: str) -> str:
    return str(uuid.UUID(v))


# --- enumerations (CHECK constraints in DOC-07) -----------------------------------------------------------
class InvestigationDepth(StrEnum):
    BASIC = "BASIC"
    DEEP = "DEEP"


class InvestigationStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    TERMINATED = "TERMINATED"
    FAILED = "FAILED"


class SessionStatus(StrEnum):
    DRAFT = "DRAFT"
    AWAITING_SETUP = "AWAITING_SETUP"
    INITIALIZING = "INITIALIZING"
    RUNNING = "RUNNING"
    PAUSING = "PAUSING"
    PAUSED = "PAUSED"
    RESUMING = "RESUMING"
    COMPLETED = "COMPLETED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class Severity(StrEnum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Confidence(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CONFIRMED = "CONFIRMED"


class VerificationStatus(StrEnum):
    DETECTED = "DETECTED"
    NEEDS_TRIAGE = "NEEDS_TRIAGE"
    UNDER_INVESTIGATION = "UNDER_INVESTIGATION"
    VERIFIED = "VERIFIED"
    FALSE_POSITIVE = "FALSE_POSITIVE"
    INCONCLUSIVE = "INCONCLUSIVE"
    ACCEPTED_RISK = "ACCEPTED_RISK"


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    SCHEDULED = "SCHEDULED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TaskType(StrEnum):
    RECON = "RECON"
    DISCOVERY = "DISCOVERY"
    PROBE = "PROBE"
    VERIFY = "VERIFY"
    ANALYZE = "ANALYZE"
    REPORT = "REPORT"


class Decision(StrEnum):
    ALLOWED = "ALLOWED"
    BLOCKED = "BLOCKED"


class Verdict(StrEnum):
    """Test verdicts (DOC-10 section 2.1)."""

    PASS = "PASS"  # noqa: S105 - not a password
    FAIL = "FAIL"
    NOT_RUN = "NOT RUN"
    PENDING_LAB_EXECUTION = "PENDING LAB EXECUTION"


# --- entities ---------------------------------------------------------------------------------------------
class TermsAcceptance(_Frozen):
    acceptance_id: str = Field(default_factory=new_id)
    terms_version: str
    terms_hash: str = Field(min_length=64, max_length=64)
    accepted_at: datetime = Field(default_factory=utcnow)
    acceptance_hash: str = Field(min_length=64, max_length=64)


class ProviderModelConfig(_Frozen):
    config_id: str = Field(default_factory=new_id)
    provider_name: str
    model_identifier: str
    display_name: str
    base_url: str | None = None
    keyring_service_key: str
    context_window_size: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    supports_tool_call: bool = False
    supports_vision: bool = False
    supports_streaming: bool = False
    is_active: bool = True
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Investigation(_Frozen):
    investigation_id: str = Field(default_factory=new_id)
    title: str = Field(min_length=1)
    description: str | None = None
    primary_target: str = Field(min_length=1)
    investigation_depth: InvestigationDepth = InvestigationDepth.BASIC
    status: InvestigationStatus = InvestigationStatus.DRAFT
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    completed_at: datetime | None = None
    terms_acceptance_id: str

    _v_id = field_validator("investigation_id", "terms_acceptance_id")(_uuid36)


class Session(_Frozen):
    session_id: str = Field(default_factory=new_id)
    investigation_id: str
    provider_config_id: str
    session_status: SessionStatus = SessionStatus.DRAFT
    relative_path: str
    total_tokens_used: int = Field(default=0, ge=0)
    total_cost_usd: float = Field(default=0.0, ge=0.0)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    ended_at: datetime | None = None

    _v_id = field_validator("session_id", "investigation_id", "provider_config_id")(_uuid36)


class Finding(_Frozen):
    finding_id: str = Field(default_factory=new_id)
    investigation_id: str
    session_id: str
    task_id: str | None = None
    stable_finding_key: str
    title: str
    summary: str
    category: str
    affected_asset: str
    affected_port: int | None = Field(default=None, ge=1, le=65535)
    severity: Severity
    confidence: Confidence
    verification_status: VerificationStatus = VerificationStatus.DETECTED
    detection_method: str
    impact_statement: str | None = None
    remediation_guidance: str | None = None
    is_included_in_report: bool = True
    first_detected_at: datetime = Field(default_factory=utcnow)
    last_verified_at: datetime = Field(default_factory=utcnow)


class NetworkAuditEvent(_Frozen):
    log_id: str = Field(default_factory=new_id)
    session_id: str
    timestamp: datetime = Field(default_factory=utcnow)
    source_ip: str
    destination_host: str
    destination_ip: str | None = None
    destination_port: int = Field(ge=0, le=65535)
    protocol: str
    decision: Decision
    block_reason: str | None = None
    bytes_transferred: int = Field(default=0, ge=0)


# Phase 4 contracts. Defined now so the schema is stable; nothing in Phases 0-3 executes them.
class Task(_Frozen):
    task_id: str = Field(default_factory=new_id)
    run_id: str
    parent_task_id: str | None = None
    task_name: str
    task_type: TaskType
    target_asset: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    priority_score: float = 50.0
    status: TaskStatus = TaskStatus.PENDING
    is_idempotent: bool = True


class ToolCall(_Frozen):
    call_id: str = Field(default_factory=new_id)
    task_id: str
    tool_name: str
    argv: tuple[str, ...]
    timeout_s: float = Field(default=300.0, gt=0)


class Observation(_Frozen):
    call_id: str
    exit_code: int | None
    stdout_path: str
    stderr_path: str
    timed_out: bool = False
    duration_s: float = Field(ge=0)


class Evidence(_Frozen):
    evidence_id: str = Field(default_factory=new_id)
    finding_id: str
    relative_path: str
    sha256: str = Field(min_length=64, max_length=64)
    description: str


class Event(_Frozen):
    event_id: str = Field(default_factory=new_id)
    session_id: str
    event_type: str
    timestamp: datetime = Field(default_factory=utcnow)
    payload: dict[str, Any] = Field(default_factory=dict)


class TestEvidence(_Frozen):
    """Six-field evidence record (DOC-10 section 2.1)."""

    __test__ = False  # not a pytest test class

    test_id: str
    title: str
    command: str
    expected: str
    actual: str
    verdict: Verdict
    evidence: str
    tier: str = "C"  # A=documentation, B=implementation (unit/mocked), C=empirical live
