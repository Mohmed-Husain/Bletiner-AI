"""InvestigationController — async bridge between the PySide6 UI and the real execution backends.

Two honest engines only:

* 'local'  — bletiner.services.recon.ReconEngine: deterministic, real tools in the Kali sandbox,
  no LLM and no API key.
* 'ai'     — the autonomous agent loop with a real provider adapter, gated by the certified egress gate
  and executed through the real Docker tool executor.

There is intentionally NO simulation path in this module. If prerequisites are missing the controller emits
an actionable error instead of fabricating tool output.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import ipaddress
import json
import logging
import re
import threading
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import docker
from bletiner.agent.events import AgentEvent, AgentState, EventType
from bletiner.agent.gate import is_gate3_certified
from bletiner.agent.loop import (
    AgentConfig,
    AgentResult,
    LoopBudget,
    RecordedFinding,
    ToolExecutor,
    ToolOutcome,
)
from bletiner.agent.steering import SteeringQueue, SteeringResult, SteeringVerdict
from bletiner.container.tool_executor import DockerToolExecutor
from bletiner.models.entities import (
    Confidence,
    Finding,
    Severity,
    VerificationStatus,
    new_id,
)
from bletiner.models.scope import AuthorizedIPTarget, ScopeSpec
from bletiner.providers.anthropic_adapter import AnthropicAdapter
from bletiner.providers.base import ModelProfile, ProviderAdapter
from bletiner.providers.deepseek_adapter import DeepSeekAdapter
from bletiner.providers.http_transport import HttpTransport
from bletiner.providers.openai_adapter import OpenAICompatibleAdapter
from bletiner.reporting.html_generator import build_html
from bletiner.reporting.markdown_builder import build_markdown
from bletiner.reporting.model import ReportContext
from bletiner.services.investigation import InvestigationRequest, InvestigationService
from bletiner.services.recon import DetectedFinding, ReconEngine
from bletiner.storage.artifacts import InvestigationPaths
from bletiner.tools.catalog import default_registry
from bletiner.tools.registry import InvestigationMode, ToolRegistry
from bletiner.ui.settings_manager import get_settings_manager

logger = logging.getLogger(__name__)

try:
    from PySide6.QtCore import QObject, Signal

    _QT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _QT_AVAILABLE = False

    class QObject:  # type: ignore[no-redef]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

    def Signal(*types: Any) -> Any:  # type: ignore[no-redef]
        return None


KALI_CONTAINER_HINTS = ("bletiner-kali", "bletiner_kali", "my-kali", "my_kali")
KALI_IMAGE_HINTS = ("bletiner-kali", "bletiner-scanner")
DEFAULT_APP_DATA = Path.home() / ".bletiner"
DEFAULT_EVIDENCE_DIR = Path("evidence/phase3")


class SignalBridge:
    """Dual interface: Qt Signal when available, plain callback list otherwise."""

    def __init__(self, qt_signal: Any = None) -> None:
        self._qt_signal = qt_signal
        self._callbacks: list[Callable[..., Any]] = []

    def connect(self, slot: Callable[..., Any]) -> None:
        if self._qt_signal is not None and hasattr(self._qt_signal, "connect"):
            with contextlib.suppress(Exception):
                self._qt_signal.connect(slot)
                return
        self._callbacks.append(slot)

    def emit(self, *args: Any, **kwargs: Any) -> None:
        if self._qt_signal is not None and hasattr(self._qt_signal, "emit"):
            with contextlib.suppress(Exception):
                self._qt_signal.emit(*args, **kwargs)
        for cb in list(self._callbacks):
            try:
                cb(*args, **kwargs)
            except Exception as exc:
                logger.exception("Error in controller callback %r: %s", cb, exc)

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        self.emit(*args, **kwargs)


@dataclass(frozen=True)
class Readiness:
    docker_running: bool
    gate_certified: bool
    kali_container: str | None
    secret_backend: str
    ai_ready: bool
    provider: str
    model: str
    ollama_running: bool
    notes: tuple[str, ...] = ()
    probe_complete: bool = False

    @property
    def local_ready(self) -> bool:
        """Deterministic local recon needs Docker + the Kali sandbox; Gate 3 governs the autonomous AI path."""
        return self.docker_running and self.kali_container is not None

    @property
    def ai_engine_ready(self) -> bool:
        """AI agent needs a configured model AND the certified fail-closed egress gate."""
        return (
            self.ai_ready and self.gate_certified and self.docker_running and self.kali_container is not None
        )


@dataclass
class InvestigationOutcome:
    investigation_id: str
    session_id: str
    target: str
    mode: str
    engine: str
    provider: str
    model: str
    findings: list[Finding]
    paths: InvestigationPaths
    reports: tuple[Path, Path] | None
    stop_reason: str
    tokens_used: int
    iterations: int
    duration_s: float
    status: str = "completed"
    notes: tuple[str, ...] = ()


def is_docker_available() -> bool:
    """Return True when a local Docker daemon answers a ping."""
    try:
        docker.from_env().ping()  # type: ignore[attr-defined]
        return True
    except Exception:
        return False


def _client_or_none() -> Any | None:
    try:
        client = docker.from_env()  # type: ignore[attr-defined]
        client.ping()
        return client
    except Exception:
        return None


def _find_kali_container(client: Any) -> Any | None:
    """Find, start, or (re)create the Kali sandbox container from a local image. Never raises."""
    try:
        for container in client.containers.list():
            name = str(getattr(container, "name", ""))
            tags = []
            image = getattr(container, "image", None)
            if image is not None:
                tags = [t for t in (getattr(image, "tags", None) or [])]
            if any(hint in name for hint in KALI_CONTAINER_HINTS) or any("bletiner-kali" in t for t in tags):
                return container
        for container in client.containers.list(all=True):
            name = str(getattr(container, "name", ""))
            if any(hint in name for hint in KALI_CONTAINER_HINTS):
                with contextlib.suppress(Exception):
                    container.start()
                    return container
        # No container exists but the sandbox image is local: provision a fresh one so the app self-heals.
        image_name = _find_kali_image(client)
        if image_name is not None:
            container = client.containers.run(
                image_name,
                command=["tail", "-f", "/dev/null"],
                name="my-kali",
                detach=True,
                auto_remove=False,
                restart_policy={"Name": "unless-stopped"},
            )
            return container
    except Exception:
        return None
    return None


def _find_kali_image(client: Any) -> str | None:
    """Return a local Kali sandbox image reference, if one exists."""
    try:
        for image in client.images.list():
            for tag in getattr(image, "tags", None) or []:
                if any(hint in tag for hint in KALI_IMAGE_HINTS):
                    return str(tag)
    except Exception:
        return None
    return None


def _clean_target(raw: str) -> str:
    value = raw.strip()
    if not value:
        return ""
    if "://" in value:
        value = value.split("://", 1)[1]
    value = value.split("/", 1)[0]
    value = value.split("?", 1)[0]
    if ":" in value and not re.fullmatch(r"[0-9a-fA-F:]+", value):
        value = value.split(":", 1)[0]
    return value.strip().rstrip(".").lower()


_HOST_RE = re.compile(
    r"^(?=.{1,253}$)([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$"
)


def _build_scope(cleaned_target: str) -> ScopeSpec:
    """Build the AgentLoop scope. Raises ValueError when the target cannot be represented (e.g. loopback)."""
    try:
        ip = ipaddress.ip_address(cleaned_target)
    except ValueError:
        return ScopeSpec(domains=(cleaned_target,))
    ip_target = AuthorizedIPTarget(cidr=f"{ip}/32", tcp_ports=(80, 443, 22, 8080))
    return ScopeSpec(ip_targets=(ip_target,), allow_private_targets=True)


class InvestigationController(QObject):
    """Bridge connecting the PySide6 UI to the real investigation backends."""

    _event_signal: Any
    _tool_output_signal: Any
    _state_change_signal: Any
    _completed_signal: Any
    _error_signal: Any
    if _QT_AVAILABLE:
        _event_signal = Signal(object)
        _tool_output_signal = Signal(str, list, str)
        _state_change_signal = Signal(object)
        _completed_signal = Signal(object)
        _error_signal = Signal(object)
    else:  # pragma: no cover
        _event_signal = None
        _tool_output_signal = None
        _state_change_signal = None
        _completed_signal = None
        _error_signal = None

    def __init__(
        self,
        *,
        app_data_dir: Path | None = None,
        evidence_dir: Path | None = None,
        registry: ToolRegistry | None = None,
        on_event: Callable[[AgentEvent], None] | None = None,
        on_tool_output: Callable[[str, list[str], str], None] | None = None,
        on_state_change: Callable[[AgentState], None] | None = None,
        on_completed: Callable[[InvestigationOutcome], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
        monitor_readiness: bool = True,
    ) -> None:
        super().__init__()
        self._app_data_dir = Path(app_data_dir) if app_data_dir is not None else DEFAULT_APP_DATA
        self._evidence_dir = Path(evidence_dir) if evidence_dir is not None else DEFAULT_EVIDENCE_DIR
        self._registry = registry or default_registry()

        self.on_event = SignalBridge(getattr(self, "_event_signal", None))
        self.on_tool_output = SignalBridge(getattr(self, "_tool_output_signal", None))
        self.on_state_change = SignalBridge(getattr(self, "_state_change_signal", None))
        self.on_completed = SignalBridge(getattr(self, "_completed_signal", None))
        self.on_error = SignalBridge(getattr(self, "_error_signal", None))
        for bridge, callback in (
            (self.on_event, on_event),
            (self.on_tool_output, on_tool_output),
            (self.on_state_change, on_state_change),
            (self.on_completed, on_completed),
            (self.on_error, on_error),
        ):
            if callback is not None:
                bridge.connect(callback)

        self._current_state = AgentState.IDLE
        self._previous_state = AgentState.IDLE
        self._is_paused = False
        self._is_stopped = False
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._active_task: asyncio.Task[InvestigationOutcome] | None = None
        self._steering_queue: SteeringQueue | None = None
        self._current_paths: InvestigationPaths | None = None
        self._current_findings: list[Finding] = []
        self._last_outcome: InvestigationOutcome | None = None
        self._active_investigation_id: str | None = None
        self._active_session_id: str | None = None
        self._raw_tool_outputs: dict[str, str] = {}
        self._current_target: str = ""
        self._current_mode: str = "deep"
        self._current_engine: str = "local"
        self._container_ref: Any | None = None
        self._store: Any | None = None
        self._store_lock = asyncio.Lock()
        self._bg_tasks: set[asyncio.Task[Any]] = set()
        self._readiness = Readiness(
            False, False, None, "unknown", False, "", "", False, ("Checking sandbox...",)
        )
        self._readiness_lock = threading.Lock()
        self._last_events: list[AgentEvent] = []
        self._stop_monitor = threading.Event()
        self._monitor: threading.Thread | None = None
        if monitor_readiness:
            self._monitor = threading.Thread(
                target=self._readiness_loop, name="bletiner-readiness", daemon=True
            )
            self._monitor.start()

    @property
    def current_state(self) -> AgentState:
        return self._current_state

    @property
    def is_running(self) -> bool:
        return (
            self._current_state
            not in (
                AgentState.IDLE,
                AgentState.COMPLETED,
                AgentState.STOPPED,
                AgentState.ERROR_HANDLING,
            )
            and not self._is_stopped
        )

    @property
    def is_paused(self) -> bool:
        return self._is_paused

    @property
    def is_stopped(self) -> bool:
        return self._is_stopped

    @property
    def current_paths(self) -> InvestigationPaths | None:
        return self._current_paths

    @property
    def current_findings(self) -> list[Finding]:
        return list(self._current_findings)

    @property
    def last_outcome(self) -> InvestigationOutcome | None:
        return self._last_outcome

    @property
    def current_target(self) -> str:
        return self._current_target

    def kali_container(self) -> Any | None:
        if self._container_ref is not None:
            with contextlib.suppress(Exception):
                if self._container_ref.status == "running":
                    return self._container_ref
        client = _client_or_none()
        container = _find_kali_container(client) if client is not None else None
        self._container_ref = container
        return container

    def _probe_readiness(self) -> Readiness:
        notes: list[str] = []
        docker_running = False
        container_name: str | None = None
        client = _client_or_none()
        if client is not None:
            docker_running = True
            container = _find_kali_container(client)
            if container is not None:
                container_name = str(getattr(container, "name", "kali"))
                self._container_ref = container
            else:
                notes.append("Kali sandbox container not found. Build or start bletiner-kali.")
        else:
            notes.append("Docker Desktop is not running.")
        gate_certified = False
        with contextlib.suppress(Exception):
            gate_certified = is_gate3_certified(self._evidence_dir)
        if not gate_certified:
            notes.append(
                "Gate 3 egress certification not found in evidence/phase3; the autonomous AI engine is disabled "
                "(local recon still works)."
            )
        settings = get_settings_manager()
        provider = str(getattr(settings.settings, "active_provider", "") or "")
        model = str(getattr(settings.settings, "active_model", "") or "")
        try:
            api_key = settings.get_api_key(provider) if provider else ""
        except Exception:
            api_key = ""
        ai_ready = bool(provider and provider not in ("fake", "none") and api_key and model)
        if not ai_ready:
            notes.append(
                f"No AI model is configured ({provider or 'no provider'} / {model or 'no model'}); "
                "use the Local recon engine or add a key in Settings."
            )
        secret_backend_raw = str(getattr(getattr(settings, "secrets", None), "backend", "memory"))
        secret_backend = {
            "keyring": "OS keyring",
            "memory": "Memory only (not persisted)",
            "dpapi": "Windows DPAPI",
        }.get(secret_backend_raw.lower(), secret_backend_raw)
        ollama_running = False
        with contextlib.suppress(Exception):
            import urllib.request

            base = str(getattr(settings.settings, "ollama_base_url", "http://localhost:11434/v1")).rstrip("/")
            if base.endswith("/v1"):
                base = base[:-3]
            if base.startswith("http://") or base.startswith("https://"):
                req = urllib.request.Request(base + "/api/tags", method="GET")  # noqa: S310
                with urllib.request.urlopen(req, timeout=1.2):  # noqa: S310 - operator-configured local URL
                    ollama_running = True
        return Readiness(
            docker_running=docker_running,
            gate_certified=gate_certified,
            kali_container=container_name,
            secret_backend=secret_backend,
            ai_ready=ai_ready,
            provider=provider,
            model=model,
            ollama_running=ollama_running,
            notes=tuple(notes),
            probe_complete=True,
        )

    def _readiness_loop(self) -> None:
        while not self._stop_monitor.is_set():
            try:
                snapshot = self._probe_readiness()
                with self._readiness_lock:
                    self._readiness = snapshot
            except Exception:
                logger.exception("readiness probe failed")
            self._stop_monitor.wait(8.0)

    def readiness(self) -> Readiness:
        with self._readiness_lock:
            return self._readiness

    def validate_target(self, raw: str) -> tuple[str | None, str | None]:
        cleaned = _clean_target(raw)
        if not cleaned:
            return None, "Enter a target domain or IPv4 address."
        if " " in cleaned or "*" in cleaned or ".." in cleaned:
            return None, "Targets must be a single hostname or IPv4 address (no spaces, wildcards or ranges)."
        try:
            ipaddress.ip_address(cleaned)
            return cleaned, None
        except ValueError:
            pass
        if not _HOST_RE.match(cleaned) or "." not in cleaned:
            return (
                None,
                "That does not look like a valid domain or IPv4 address (e.g. example.com or 10.0.0.5).",
            )
        return cleaned, None

    def target_in_authorized_scope(self, cleaned: str, authorized: list[str]) -> bool:
        cleaned = cleaned.strip().lower()
        try:
            ip = ipaddress.ip_address(cleaned)
        except ValueError:
            ip = None
        for entry in authorized:
            raw_entry = str(entry).strip()
            if not raw_entry:
                continue
            if ip is not None:
                try:
                    if ip in ipaddress.ip_network(raw_entry, strict=False):
                        return True
                except ValueError:
                    pass
            candidate = _clean_target(raw_entry)
            if not candidate:
                continue
            if ip is None and (cleaned == candidate or cleaned.endswith("." + candidate)):
                return True
        return False

    async def _ensure_store(self) -> Any:
        if self._store is not None:
            return self._store
        async with self._store_lock:
            if self._store is None:
                from bletiner.services.session_store import SessionStore

                store = SessionStore(self._app_data_dir, self._evidence_dir)
                await store.open()
                self._store = store
            return self._store

    async def sessions(self, limit: int = 100) -> list[Any]:
        store = await self._ensure_store()
        return list(await store.list_summaries(limit))

    async def findings_for(self, investigation_id: str) -> list[Finding]:
        if investigation_id == self._active_investigation_id and self._current_findings:
            return list(self._current_findings)
        try:
            store = await self._ensure_store()
            return list(await store.load_findings(investigation_id))
        except Exception:
            return []

    async def delete_session(self, investigation_id: str) -> None:
        store = await self._ensure_store()
        await store.delete(investigation_id)
        if investigation_id == self._active_investigation_id:
            self._current_findings = []
            self._current_paths = None

    def _set_state(self, state: AgentState) -> None:
        self._current_state = state
        self.on_state_change.emit(state)

    def _handle_event(self, event: AgentEvent) -> None:
        self._last_events.append(event)
        if event.type == EventType.STATE_CHANGED:
            raw_state = str(event.payload.get("state", ""))
            with contextlib.suppress(ValueError):
                self._current_state = AgentState(raw_state)
        elif event.type == EventType.FINDING:
            self._collect_finding_from_event(event)
        self.on_event.emit(event)

    def _collect_finding_from_event(self, event: AgentEvent) -> None:
        if not self._active_investigation_id or not self._active_session_id:
            return
        payload = event.payload
        severity = Severity.__members__.get(str(payload.get("severity", "INFO")).upper(), Severity.INFO)
        confidence = Confidence.__members__.get(
            str(payload.get("confidence", "MEDIUM")).upper(), Confidence.MEDIUM
        )
        verification = (
            VerificationStatus.VERIFIED
            if str(payload.get("verification", "VERIFIED")).upper() == "VERIFIED"
            else VerificationStatus.NEEDS_TRIAGE
        )
        finding = self._make_finding(
            title=str(payload.get("title", "Finding")),
            summary=str(payload.get("summary", "")),
            asset=str(payload.get("asset", self._current_target)),
            severity=severity,
            confidence=confidence,
            verification=verification,
            category=str(payload.get("category", "Vulnerability")),
            remediation=str(payload.get("remediation", "")),
            detection_method=f"{self._current_engine}_engine",
        )
        key = (finding.title.lower(), finding.affected_asset.lower(), finding.affected_port)
        if any(
            (f.title.lower(), f.affected_asset.lower(), f.affected_port) == key
            for f in self._current_findings
        ):
            return
        self._current_findings.append(finding)

    def _handle_tool_output(self, tool_name: str, argv: list[str], output: str) -> None:
        if tool_name != "[SYSTEM]" and output:
            self._raw_tool_outputs[tool_name] = output[:300_000]
        self.on_tool_output.emit(tool_name, argv, output)

    def _make_finding(
        self,
        *,
        title: str,
        summary: str,
        asset: str,
        severity: Severity,
        confidence: Confidence,
        verification: VerificationStatus,
        category: str,
        remediation: str = "",
        detection_method: str = "local_engine",
    ) -> Finding:
        affected_asset, affected_port = self._split_asset(asset)
        stable = hashlib.sha256(
            f"{title.strip().lower()}|{affected_asset.lower()}|{affected_port or ''}".encode()
        ).hexdigest()
        return Finding(
            investigation_id=self._active_investigation_id or new_id(),
            session_id=self._active_session_id or new_id(),
            stable_finding_key=stable,
            title=title[:300],
            summary=summary[:4000],
            category=category or "Vulnerability",
            affected_asset=affected_asset,
            affected_port=affected_port,
            severity=severity,
            confidence=confidence,
            verification_status=verification,
            detection_method=detection_method,
            remediation_guidance=(remediation or None),
            is_included_in_report=True,
        )

    @staticmethod
    def _split_asset(asset: str) -> tuple[str, int | None]:
        text = str(asset).strip()
        if "://" in text:
            parsed = urllib.parse.urlsplit(text)
            host = parsed.hostname or text
            try:
                port = parsed.port
            except ValueError:
                port = None
            return host, port
        if ":" in text:
            host, _, maybe_port = text.rpartition(":")
            if maybe_port.isdigit() and 0 < int(maybe_port) <= 65535:
                return host, int(maybe_port)
        return text, None

    def _domain_findings_from_detected(self, items: list[DetectedFinding]) -> list[Finding]:
        findings: list[Finding] = []
        for item in items:
            severity = Severity.__members__.get(str(item.severity).upper(), Severity.INFO)
            confidence = Confidence.__members__.get(str(item.confidence).upper(), Confidence.MEDIUM)
            verification = (
                VerificationStatus.VERIFIED
                if str(item.verification).upper() == "VERIFIED"
                else VerificationStatus.NEEDS_TRIAGE
            )
            findings.append(
                self._make_finding(
                    title=item.title,
                    summary=item.summary,
                    asset=item.asset,
                    severity=severity,
                    confidence=confidence,
                    verification=verification,
                    category=item.category,
                    remediation=item.remediation,
                    detection_method="local_recon",
                )
            )
        deduped: dict[str, Finding] = {}
        for finding in findings:
            deduped.setdefault(finding.stable_finding_key, finding)
        return list(deduped.values())

    def pause(self) -> bool:
        if self._is_paused:
            self.resume()
            return False
        self._is_paused = True
        self._pause_event.clear()
        self._previous_state = self._current_state
        self._set_state(AgentState.PAUSED)
        return True

    def resume(self) -> None:
        if not self._is_paused:
            return
        self._is_paused = False
        self._pause_event.set()
        resumed = self._previous_state if self._previous_state != AgentState.PAUSED else AgentState.EXECUTING
        self._set_state(resumed)

    def stop(self) -> None:
        self._is_stopped = True
        self._is_paused = False
        self._pause_event.set()
        if self._active_task is not None and not self._active_task.done():
            self._active_task.cancel()
        else:
            self._set_state(AgentState.STOPPED)

    def submit_steering(self, instruction: str) -> SteeringResult:
        if self._current_engine != "ai" or self._steering_queue is None:
            return SteeringResult(
                verdict=SteeringVerdict.REJECTED_SAFETY,
                instruction=instruction,
                reason=(
                    "The deterministic local engine does not accept steering. "
                    "Switch to the AI engine for live steering."
                ),
            )
        result = self._steering_queue.submit(instruction)
        if not result.accepted:
            self.on_event.emit(
                AgentEvent(
                    EventType.STEERING_REJECTED, payload={"instruction": instruction, "reason": result.reason}
                )
            )
        return result

    async def execute_terminal_command(self, cmd: str) -> tuple[int, str, str]:
        command = cmd.strip()
        if not command:
            return 0, "", ""
        container = await asyncio.to_thread(self.kali_container)
        if container is None:
            return (
                1,
                "",
                "[Sandbox offline] Docker Desktop or the Kali container is not running. Start Docker and retry.",
            )
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._exec_terminal, container, command), timeout=120.0
            )
        except TimeoutError:
            return 124, "", "[timeout] Command exceeded 120s."
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return 1, "", f"Execution error: {exc}"

    @staticmethod
    def _exec_terminal(container: Any, command: str) -> tuple[int, str, str]:
        result = container.exec_run(
            cmd=["/bin/bash", "-lc", command],
            user="bletiner",
            workdir="/workspace",
            stdout=True,
            stderr=True,
            demux=True,
        )
        exit_code = int(getattr(result, "exit_code", result[0]))
        output = getattr(result, "output", result[1])
        out_b, err_b = output if isinstance(output, tuple) else (output, b"")
        return exit_code, (out_b or b"").decode("utf-8", "replace"), (err_b or b"").decode("utf-8", "replace")

    async def start(
        self,
        *,
        target: str,
        mode: str = "deep",
        engine: str = "local",
        provider: str = "",
        model: str = "",
        api_key: str | None = None,
        objective: str = "",
    ) -> InvestigationOutcome:
        self._reset_run_state()
        cleaned, error = self.validate_target(target)
        if error or cleaned is None:
            raise ValueError(error or "Invalid target.")
        settings = get_settings_manager()
        authorized = list(getattr(settings.settings, "authorized_scope", []) or [])
        if not self.target_in_authorized_scope(cleaned, authorized):
            raise ValueError(
                f"Target '{cleaned}' is outside the authorized scope "
                f"({', '.join(authorized) or 'empty'}). Add it under Targets first."
            )
        engine_name = "ai" if str(engine).lower() == "ai" else "local"
        mode_name = "deep" if str(mode).lower().startswith("deep") else "basic"
        provider_name = ""
        model_name = ""
        provider_adapter: ProviderAdapter | None = None

        if engine_name == "ai":
            provider_name = provider or str(getattr(settings.settings, "active_provider", ""))
            model_name = model or str(getattr(settings.settings, "active_model", ""))
            if not provider_name or provider_name in ("fake", "none"):
                raise ValueError(
                    "No AI provider is configured. Open Settings and select a provider and model."
                )
            if not model_name:
                raise ValueError("No AI model is selected. Open Models and choose a model.")
            key = api_key or settings.get_api_key(provider_name)
            if not key:
                raise ValueError(
                    f"No API key configured for {provider_name}. Add one in Settings, or use the Local recon engine."
                )
            provider_adapter = self._build_provider(provider_name, str(key), model_name)

        container = await asyncio.to_thread(self.kali_container)
        if container is None:
            raise RuntimeError(
                "The Kali sandbox is not available. Start Docker Desktop and make sure the bletiner-kali "
                "container exists, then retry."
            )
        self._container_ref = container

        store = await self._ensure_store()
        record = await store.create(
            target=cleaned,
            mode=mode_name,
            engine=engine_name,
            provider=provider_name,
            model=model_name,
            objective=objective or f"Security investigation of {cleaned}",
        )
        self._active_investigation_id = record.investigation_id
        self._active_session_id = record.session_id
        self._current_paths = record.paths
        self._current_target = cleaned
        self._current_mode = mode_name
        self._current_engine = engine_name
        self._raw_tool_outputs = {}
        self._last_events = []
        self._steering_queue = None  # AI engine sets it once the scope is validated
        self._set_state(AgentState.INITIALIZING)

        executor = _PauseAwareExecutor(
            DockerToolExecutor(container),
            pause_event=self._pause_event,
            check_stopped=lambda: self._is_stopped,
        )
        started = time.monotonic()
        status = "completed"
        stop_reason = "completed"
        iterations = 0
        tokens = 0
        notes: list[str] = []
        findings: list[Finding] = []
        try:
            if engine_name == "local":
                recon = ReconEngine(
                    executor,
                    self._registry,
                    on_event=self._handle_event,
                    on_tool_output=self._handle_tool_output,
                    pause_event=self._pause_event,
                    check_stopped=lambda: self._is_stopped,
                )
                result = await recon.run(cleaned, mode=mode_name, objective=objective)
                iterations = result.iterations
                stop_reason = result.stop_reason
                notes = list(result.notes)
                findings = self._domain_findings_from_detected(result.findings)
                if not findings:
                    findings = list(self._current_findings)
                self._current_findings = findings
            elif engine_name == "ai":
                if provider_adapter is None:
                    raise RuntimeError("AI provider adapter is not configured.")
                try:
                    ai_scope = _build_scope(cleaned)
                except ValueError as exc:
                    raise ValueError(
                        f"The AI engine cannot use this target ({exc}). AI mode requires a domain or public IP; "
                        "use the Local recon engine for loopback/private labs."
                    ) from exc
                config = AgentConfig(
                    profile=self._profile_for(provider_name, model_name),
                    mode=InvestigationMode.DEEP if mode_name == "deep" else InvestigationMode.BASIC,
                    budget=LoopBudget(max_iterations=18, max_tokens=120_000),
                    system_prompt=(
                        "You are Bletiner AI, an autonomous, non-destructive security investigator running "
                        "inside an authorized Kali Linux sandbox. Use the provided tools to gather real "
                        "evidence, then record findings with record_finding and finish with "
                        "finish_investigation. Never fabricate tool output."
                    ),
                )
                request = InvestigationRequest(
                    objective=objective or f"Investigate the security posture of {cleaned}",
                    scope=ai_scope,
                    config=config,
                )
                self._steering_queue = SteeringQueue(ai_scope)
                service = InvestigationService(
                    provider=provider_adapter,
                    registry=self._registry,
                    evidence_dir=self._evidence_dir,
                    on_event=self._handle_event,
                )
                agent_result: AgentResult = await service.start(
                    request, executor, steering=self._steering_queue
                )
                iterations = agent_result.iterations
                tokens = agent_result.tokens_used
                stop_reason = agent_result.stop_reason
                if not self._current_findings:
                    self._current_findings = self._convert_agent_findings(agent_result.findings)
                findings = list(self._current_findings)
            if self._is_stopped:
                status = "stopped"
                stop_reason = "stopped_by_operator"
        except asyncio.CancelledError:
            status = "stopped"
            stop_reason = "stopped_by_operator"
            findings = list(self._current_findings)
            self._is_stopped = True
        except Exception as exc:
            self._set_state(AgentState.ERROR_HANDLING)
            with contextlib.suppress(Exception):
                await store.fail(record, error=str(exc))
            outcome = InvestigationOutcome(
                investigation_id=record.investigation_id,
                session_id=record.session_id,
                target=cleaned,
                mode=mode_name,
                engine=engine_name,
                provider=provider_name,
                model=model_name,
                findings=list(self._current_findings),
                paths=record.paths,
                reports=None,
                stop_reason="error",
                tokens_used=tokens,
                iterations=iterations,
                duration_s=time.monotonic() - started,
                status="error",
                notes=(str(exc),),
            )
            self._last_outcome = outcome
            self.on_error.emit(exc)
            raise
        finally:
            self._pause_event.set()

        duration = time.monotonic() - started
        reports = await self._write_artifacts(
            record.paths,
            target=cleaned,
            mode=mode_name,
            findings=findings,
            raw_outputs=dict(self._raw_tool_outputs),
            events=list(self._last_events),
            record=record,
            stop_reason=stop_reason,
        )
        with contextlib.suppress(Exception):
            await store.complete(
                record,
                status=status,
                stop_reason=stop_reason,
                iterations=iterations,
                tokens_used=tokens,
                findings=findings,
                reports=list(reports),
            )
        final_status = "stopped" if status == "stopped" else "completed"
        self._set_state(AgentState.STOPPED if final_status == "stopped" else AgentState.COMPLETED)
        outcome = InvestigationOutcome(
            investigation_id=record.investigation_id,
            session_id=record.session_id,
            target=cleaned,
            mode=mode_name,
            engine=engine_name,
            provider=provider_name,
            model=model_name,
            findings=findings,
            paths=record.paths,
            reports=reports,
            stop_reason=stop_reason,
            tokens_used=tokens,
            iterations=iterations,
            duration_s=duration,
            status=final_status,
            notes=tuple(notes),
        )
        self._last_outcome = outcome
        self.on_completed.emit(outcome)
        return outcome

    def _convert_agent_findings(self, recorded: list[RecordedFinding]) -> list[Finding]:
        findings: list[Finding] = []
        for item in recorded:
            severity = Severity.__members__.get(str(item.severity).upper(), Severity.INFO)
            confidence = Confidence.__members__.get(str(item.confidence).upper(), Confidence.MEDIUM)
            findings.append(
                self._make_finding(
                    title=item.title or "AI recorded finding",
                    summary=item.summary,
                    asset=item.asset or self._current_target,
                    severity=severity,
                    confidence=confidence,
                    verification=VerificationStatus.VERIFIED,
                    category="Vulnerability",
                    detection_method="ai_agent",
                )
            )
        return findings

    def _profile_for(self, provider: str, model: str) -> ModelProfile:
        settings = get_settings_manager()
        base_url: str | None = None
        if provider == "deepseek":
            base_url = "https://api.deepseek.com"
        elif provider == "openai":
            base_url = "https://api.openai.com/v1"
        elif provider == "anthropic":
            base_url = "https://api.anthropic.com/v1"
        elif provider == "ollama":
            base_url = str(getattr(settings.settings, "ollama_base_url", "http://localhost:11434/v1"))
        return ModelProfile(
            provider=provider,
            model=model,
            base_url=base_url,
            context_window=64_000,
            max_output_tokens=4096,
        )

    def _build_provider(self, provider: str, key: str, model: str) -> ProviderAdapter:
        transport = HttpTransport(timeout_s=120.0)
        if provider == "anthropic":
            return AnthropicAdapter(transport, api_key=key)
        if provider == "deepseek":
            return DeepSeekAdapter(transport, api_key=key)
        if provider in ("openai", "openrouter", "groq", "together", "mistral", "custom"):
            return OpenAICompatibleAdapter(transport, api_key=key)
        if provider == "ollama":
            return OpenAICompatibleAdapter(transport, api_key=key or "ollama")
        raise ValueError(f"Unsupported AI provider '{provider}'.")

    async def _write_artifacts(
        self,
        paths: InvestigationPaths,
        *,
        target: str,
        mode: str,
        findings: list[Finding],
        raw_outputs: dict[str, str],
        events: list[AgentEvent],
        record: Any,
        stop_reason: str,
    ) -> tuple[Path, Path]:
        return await asyncio.to_thread(
            self._write_artifacts_sync,
            paths,
            target,
            mode,
            findings,
            raw_outputs,
            events,
            stop_reason,
        )

    def _write_artifacts_sync(
        self,
        paths: InvestigationPaths,
        target: str,
        mode: str,
        findings: list[Finding],
        raw_outputs: dict[str, str],
        events: list[AgentEvent],
        stop_reason: str,
    ) -> tuple[Path, Path]:
        for directory in (paths.reports, paths.evidence, paths.logs):
            directory.mkdir(parents=True, exist_ok=True)
        scope_entries = (target,)
        context = ReportContext(
            title=f"Investigation Report: {target}",
            primary_target=target,
            authorized_scope=scope_entries,
            findings=tuple(findings),
        )
        markdown_path = paths.reports / "report.md"
        html_path = paths.reports / "report.html"
        markdown_path.write_text(build_markdown(context), encoding="utf-8")
        html_path.write_text(build_html(context), encoding="utf-8")

        (paths.evidence / "findings.json").write_text(
            json.dumps([f.model_dump(mode="json") for f in findings], indent=2, default=str),
            encoding="utf-8",
        )
        manifest: list[dict[str, Any]] = []
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        for tool_name, output in raw_outputs.items():
            safe_tool = re.sub(r"[^A-Za-z0-9_.-]+", "_", tool_name)[:60] or "tool"
            evidence_path = paths.evidence / f"{safe_tool}_{stamp}.txt"
            evidence_path.write_text(output, encoding="utf-8")
            digest = hashlib.sha256(output.encode("utf-8")).hexdigest()
            manifest.append(
                {
                    "tool": tool_name,
                    "file": evidence_path.name,
                    "bytes": len(output.encode("utf-8")),
                    "sha256": digest,
                }
            )
        (paths.evidence / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        events_payload = [
            {"timestamp": e.timestamp.isoformat(), "type": e.type.value, "payload": e.payload} for e in events
        ]
        (paths.logs / "agent_runtime.json").write_text(
            json.dumps(events_payload, indent=2, default=str), encoding="utf-8"
        )
        (paths.logs / "recon.log").write_text(
            "\n".join(
                [
                    f"target={target}",
                    f"mode={mode}",
                    f"engine={self._current_engine}",
                    f"stop_reason={stop_reason}",
                    f"findings={len(findings)}",
                    f"tools={','.join(raw_outputs)}",
                ]
            ),
            encoding="utf-8",
        )
        return markdown_path, html_path

    def _reset_run_state(self) -> None:
        self._is_stopped = False
        self._is_paused = False
        self._pause_event.set()
        self._current_findings = []
        self._current_paths = None
        self._raw_tool_outputs = {}
        self._last_events = []
        self._steering_queue = None
        self._active_investigation_id = None
        self._active_session_id = None

    async def aclose(self) -> None:
        """Close the session store and stop the readiness monitor (call from the event loop)."""
        self._stop_monitor.set()
        self._is_stopped = True
        if self._store is not None:
            with contextlib.suppress(Exception):
                await self._store.close()
            self._store = None

    def shutdown(self) -> None:
        self._stop_monitor.set()
        self._is_stopped = True
        with contextlib.suppress(Exception):
            if self._monitor is not None:
                self._monitor.join(timeout=1.0)
        with contextlib.suppress(Exception):
            loop = asyncio.get_event_loop()
            if self._store is not None and loop.is_running():
                task = asyncio.ensure_future(self._store.close())
                self._bg_tasks.add(task)
                task.add_done_callback(self._bg_tasks.discard)


class _PauseAwareExecutor:
    """Wraps a ToolExecutor: waits while paused and aborts between tools when stopped."""

    def __init__(
        self, inner: ToolExecutor, *, pause_event: asyncio.Event, check_stopped: Callable[[], bool]
    ) -> None:
        self._inner = inner
        self._pause_event = pause_event
        self._check_stopped = check_stopped

    async def run(self, tool_name: str, argv: list[str], timeout_s: float) -> ToolOutcome:
        if self._check_stopped():
            raise asyncio.CancelledError("Investigation stopped by operator.")
        await self._pause_event.wait()
        outcome = await self._inner.run(tool_name, argv, timeout_s)
        await self._pause_event.wait()
        if self._check_stopped():
            raise asyncio.CancelledError("Investigation stopped by operator.")
        return outcome
