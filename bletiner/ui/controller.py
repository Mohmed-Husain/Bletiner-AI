"""InvestigationController — async bridge between PySide6 desktop UI and the autonomous agent loop.

Connects the desktop UI to InvestigationService and AgentLoop via qasync/asyncio without blocking Qt.
Supports both live Docker execution (when Gate 3 certified) and deterministic simulation mode.
Emits Qt signals and callbacks for operational events, tool outputs, state changes, and generated reports.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import ipaddress
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import docker
from bletiner.agent.events import AgentEvent, AgentState, EventType
from bletiner.agent.gate import is_gate3_certified
from bletiner.agent.loop import (
    AgentConfig,
    AgentLoop,
    AgentResult,
    LoopBudget,
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
from bletiner.providers.base import (
    AgentMessage,
    ModelProfile,
    ProviderAdapter,
    ProviderResponse,
    Role,
    ToolUse,
    Usage,
)
from bletiner.providers.deepseek_adapter import DeepSeekAdapter
from bletiner.providers.fake import FakeProvider
from bletiner.providers.http_transport import HttpTransport
from bletiner.providers.openai_adapter import OpenAICompatibleAdapter
from bletiner.reporting.html_generator import build_html
from bletiner.reporting.markdown_builder import build_markdown
from bletiner.reporting.model import ReportContext
from bletiner.services.investigation import InvestigationRequest, InvestigationService
from bletiner.storage.artifacts import ArtifactStore, InvestigationPaths
from bletiner.tools.catalog import default_registry
from bletiner.tools.registry import InvestigationMode, ToolRegistry

logger = logging.getLogger(__name__)

# --- Qt Signal Fallback / Compatibility Layer -----------------------------------------------------------
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


class SignalBridge:
    """Provides a dual interface: Qt Signal (.connect / .emit) and Python Callable."""

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
                logger.exception("Error in callback %r: %s", cb, exc)

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        self.emit(*args, **kwargs)


# --- Simulation Tool Executor -------------------------------------------------------------------------
class SimulatedToolExecutor:
    """Deterministic, non-destructive tool simulator for offline/local-lab UI execution.

    Produces authentic terminal stdout/stderr for standard network security tools (nmap, nikto, whois, etc.)
    and notifies callbacks without requiring a live Kali container runtime.
    """

    def __init__(
        self,
        on_output: Callable[[str, list[str], str], None] | None = None,
        *,
        step_delay_s: float = 0.25,
    ) -> None:
        self._on_output = on_output
        self._step_delay_s = step_delay_s

    async def run(self, tool_name: str, argv: list[str], timeout_s: float) -> ToolOutcome:
        if self._step_delay_s > 0:
            await asyncio.sleep(self._step_delay_s)

        target = argv[-1] if argv else "target"
        output = self._render_output(tool_name, argv, target)

        if self._on_output is not None:
            self._on_output(tool_name, argv, output)

        return ToolOutcome(exit_code=0, stdout=output, stderr="")

    def _render_output(self, tool_name: str, argv: list[str], target: str) -> str:
        cmd_str = " ".join(argv)
        match tool_name:
            case "nmap_fast":
                return (
                    f"Starting Nmap 7.94 ( https://nmap.org ) at 2026-11-16 12:05 UTC\n"
                    f"Nmap scan report for {target}\n"
                    f"Host is up (0.024s latency).\n"
                    f"Not shown: 97 closed tcp ports (conn-refused)\n"
                    f"PORT    STATE SERVICE\n"
                    f"22/tcp  open  ssh\n"
                    f"80/tcp  open  http\n"
                    f"443/tcp open  https\n\n"
                    f"Nmap done: 1 IP address (1 host up) scanned in 1.48 seconds\n"
                )
            case "nmap_service":
                return (
                    f"Starting Nmap 7.94 ( https://nmap.org ) at 2026-11-16 12:05 UTC\n"
                    f"Nmap scan report for {target}\n"
                    f"Host is up (0.026s latency).\n"
                    f"PORT    STATE SERVICE  VERSION\n"
                    f"22/tcp  open  ssh      OpenSSH 8.9p1 Ubuntu 3ubuntu0.7 (Ubuntu Linux; protocol 2.0)\n"
                    f"80/tcp  open  http     Apache httpd 2.4.58 ((Ubuntu))\n"
                    f"443/tcp open  https    Apache httpd 2.4.58 (SSL)\n"
                    f"Service Info: OS: Linux; CPE: cpe:/o:linux:linux_kernel\n\n"
                    f"Service detection performed. Please report any incorrect results at nmap.org/submit .\n"
                    f"Nmap done: 1 IP address (1 host up) scanned in 5.32 seconds\n"
                )
            case "nikto":
                return (
                    f"- Nikto v2.5.0\n"
                    f"---------------------------------------------------------------------------\n"
                    f"+ Target IP:          {target}\n"
                    f"+ Target Hostname:    {target}\n"
                    f"+ Target Port:        80\n"
                    f"---------------------------------------------------------------------------\n"
                    f"+ Server: Apache/2.4.58 (Ubuntu)\n"
                    f"+ The anti-clickjacking X-Frame-Options header is not present.\n"
                    f"+ The X-Content-Type-Options header is not set.\n"
                    f"+ Root page / redirects to /index.html\n"
                    f"+ OSVDB-3092: /: Apache/2.4.58 appears to be outdated.\n"
                    f"+ 7916 requests: 0 error(s) and 3 item(s) reported on remote host\n"
                    f"---------------------------------------------------------------------------\n"
                    f"+ 1 host(s) tested\n"
                )
            case "whatweb":
                return (
                    f"http://{target} [200 OK] Apache[2.4.58], HTTPServer[Ubuntu Linux][Apache/2.4.58], "
                    f"OpenSSL[3.0.2], Title[Welcome to Test Lab]\n"
                )
            case "whois":
                return (
                    f"Domain Name: {target}\n"
                    f"Registry Domain ID: 2138514_DOMAIN_COM-VRSN\n"
                    f"Registrar: MarkMonitor Inc.\n"
                    f"Status: clientTransferProhibited\n"
                    f"Name Server: NS1.{target}\n"
                    f"DNSSEC: unsigned\n"
                )
            case "dig":
                return f";; ANSWER SECTION:\n{target}. 300 IN A 93.184.216.34\n"
            case "http_head":
                return (
                    "HTTP/1.1 200 OK\r\n"
                    "Date: Mon, 16 Nov 2026 12:05:00 GMT\r\n"
                    "Server: Apache/2.4.58 (Ubuntu)\r\n"
                    "Content-Type: text/html; charset=UTF-8\r\n"
                    "Connection: close\r\n"
                )
            case "nuclei_cve":
                return (
                    f"[CVE-2023-25690] [http] [critical] http://{target}/ "
                    "[Apache HTTP Server Request Smuggling]\n"
                )
            case _:
                return f"[exec: {cmd_str}]\nStatus: completed successfully (exit code 0).\n"


# --- Tool Executor Output Wrapper ---------------------------------------------------------------------
class ToolExecutorNotifier:
    """Wraps any ToolExecutor to emit live output signals and observe pausing/stopping."""

    def __init__(
        self,
        inner: ToolExecutor,
        on_output: Callable[[str, list[str], str], None],
        pause_event: asyncio.Event,
        check_stopped: Callable[[], bool],
    ) -> None:
        self._inner = inner
        self._on_output = on_output
        self._pause_event = pause_event
        self._check_stopped = check_stopped

    async def run(self, tool_name: str, argv: list[str], timeout_s: float) -> ToolOutcome:
        if self._check_stopped():
            raise asyncio.CancelledError("Investigation stopped by operator.")

        # Respect paused state before launching tool
        await self._pause_event.wait()

        outcome = await self._inner.run(tool_name, argv, timeout_s)

        out_text = outcome.stdout if outcome.ok else f"[exit {outcome.exit_code}] {outcome.stderr}"
        self._on_output(tool_name, argv, out_text)

        # Respect paused state after tool completion
        await self._pause_event.wait()
        return outcome


# --- Helper functions ---------------------------------------------------------------------------------
def is_docker_available() -> bool:
    """Check if a local Docker daemon is reachable."""
    try:
        docker.from_env().ping()
        return True
    except Exception:
        return False


def _find_or_start_kali_container() -> Any | None:
    """Find an active Kali container or start an existing one."""
    try:
        client = docker.from_env()
        for c in client.containers.list():
            if any(k in c.name for k in ("bletiner-kali", "bletiner_kali", "my-kali")):
                return c
            tags = c.image.tags if c.image else []
            if any("bletiner-kali" in t or "bletiner-scanner" in t for t in tags):
                return c
        for c in client.containers.list(all=True):
            if any(k in c.name for k in ("bletiner-kali", "bletiner_kali", "my-kali")):
                c.start()
                return c
    except Exception:
        pass
    return None


def _clean_target(raw: str) -> str:
    cleaned = raw.strip()
    if "://" in cleaned:
        cleaned = cleaned.split("://", 1)[1]
    cleaned = cleaned.split("/", 1)[0]
    cleaned = cleaned.split(":", 1)[0]
    return cleaned.strip()


def _build_scope(cleaned_target: str) -> ScopeSpec:
    try:
        ip = ipaddress.ip_address(cleaned_target)
        cidr = f"{ip}/32"
        ip_target = AuthorizedIPTarget(cidr=cidr, tcp_ports=(80, 443, 22, 8080))
        return ScopeSpec(ip_targets=(ip_target,), allow_private_targets=True)
    except ValueError:
        return ScopeSpec(domains=(cleaned_target,))


def _build_scripted_responses(target: str, mode: InvestigationMode) -> list[ProviderResponse]:
    """Build a deterministic simulation conversation sequence."""
    script = [
        ProviderResponse(
            AgentMessage(
                Role.ASSISTANT,
                f"Starting active reconnaissance on authorized target: {target}.",
                (ToolUse("call_1", "nmap_fast", {"target": target}),),
            ),
            Usage(110, 45),
        ),
        ProviderResponse(
            AgentMessage(
                Role.ASSISTANT,
                "Ports 22, 80, 443 detected. Fingerprinting running services and version banners.",
                (ToolUse("call_2", "nmap_service", {"target": target}),),
            ),
            Usage(160, 60),
        ),
        ProviderResponse(
            AgentMessage(
                Role.ASSISTANT,
                "Web server identified as Apache 2.4.58. Conducting web server configuration audit.",
                (ToolUse("call_3", "nikto", {"target": target}),),
            ),
            Usage(190, 75),
        ),
    ]

    findings = [
        ToolUse(
            "call_f1",
            "record_finding",
            {
                "title": "Outdated Apache 2.4.58 HTTP Server",
                "severity": "MEDIUM",
                "confidence": "HIGH",
                "asset": f"{target}:80",
                "summary": "Apache 2.4.58 has known security advisories including request smuggling.",
            },
        ),
        ToolUse(
            "call_f2",
            "record_finding",
            {
                "title": "OpenSSH 8.9p1 Banner Disclosure",
                "severity": "LOW",
                "confidence": "CONFIRMED",
                "asset": f"{target}:22",
                "summary": "OpenSSH banner discloses Ubuntu package version to unauthenticated clients.",
            },
        ),
    ]

    if mode == InvestigationMode.DEEP:
        findings.append(
            ToolUse(
                "call_f3",
                "record_finding",
                {
                    "title": "Missing Anti-Clickjacking X-Frame-Options Header",
                    "severity": "INFO",
                    "confidence": "HIGH",
                    "asset": f"{target}:443",
                    "summary": "The web application does not specify an X-Frame-Options header.",
                },
            )
        )

    script.append(
        ProviderResponse(
            AgentMessage(
                Role.ASSISTANT,
                "Recording identified vulnerabilities and observations.",
                tuple(findings),
            ),
            Usage(240, 110),
        )
    )

    script.append(
        ProviderResponse(
            AgentMessage(
                Role.ASSISTANT,
                "Investigation objective completed. Concluding scan and generating final report.",
                (ToolUse("call_end", "finish_investigation", {}),),
            ),
            Usage(95, 30),
        )
    )

    return script


# --- InvestigationController --------------------------------------------------------------------------
class InvestigationController(QObject):
    """Bridge connecting PySide6 desktop UI to the autonomous investigation loop.

    Signals:
      on_event(AgentEvent): operational events emitted by the agent loop.
      on_tool_output(tool_name: str, argv: list[str], output: str): terminal stream lines.
      on_state_change(state: AgentState): agent FSM state transitions.
      on_completed(result: AgentResult, paths: InvestigationPaths): investigation completion.
      on_error(error: Exception): unhandled runtime errors.
    """

    if _QT_AVAILABLE:
        _event_signal = Signal(object)
        _tool_output_signal = Signal(str, list, str)
        _state_change_signal = Signal(object)
        _completed_signal = Signal(object, object)
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
        on_completed: Callable[[AgentResult, InvestigationPaths], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        super().__init__()
        self._app_data_dir = app_data_dir or (Path.home() / ".bletiner")
        self._evidence_dir = evidence_dir or Path("evidence/phase3")
        self._registry = registry or default_registry()

        # Signal bridges
        self.on_event = SignalBridge(getattr(self, "_event_signal", None))
        self.on_tool_output = SignalBridge(getattr(self, "_tool_output_signal", None))
        self.on_state_change = SignalBridge(getattr(self, "_state_change_signal", None))
        self.on_completed = SignalBridge(getattr(self, "_completed_signal", None))
        self.on_error = SignalBridge(getattr(self, "_error_signal", None))

        # Wire optional callbacks passed directly
        if on_event is not None:
            self.on_event.connect(on_event)
        if on_tool_output is not None:
            self.on_tool_output.connect(on_tool_output)
        if on_state_change is not None:
            self.on_state_change.connect(on_state_change)
        if on_completed is not None:
            self.on_completed.connect(on_completed)
        if on_error is not None:
            self.on_error.connect(on_error)

        # Execution state
        self._current_state = AgentState.IDLE
        self._previous_state = AgentState.IDLE
        self._is_paused = False
        self._is_stopped = False
        self._pause_event = asyncio.Event()
        self._pause_event.set()  # running by default

        self._steering_queue: SteeringQueue | None = None
        self._current_paths: InvestigationPaths | None = None
        self._last_result: AgentResult | None = None
        self._active_task: asyncio.Task[AgentResult] | None = None
        self._service: InvestigationService | None = None

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
    def last_result(self) -> AgentResult | None:
        return self._last_result

    def is_live_ready(self) -> bool:
        """Return True if Docker is running AND Gate 3 is certified for live Kali execution."""
        return is_docker_available() and is_gate3_certified(self._evidence_dir)

    def pause(self) -> bool:
        """Toggle pause state. Returns True if now paused, False if resumed."""
        if self._is_paused:
            self.resume()
            return False
        self._is_paused = True
        self._pause_event.clear()
        self._previous_state = self._current_state
        self._current_state = AgentState.PAUSED
        self.on_state_change.emit(AgentState.PAUSED)
        return True

    def resume(self) -> None:
        """Resume investigation if paused."""
        if self._is_paused:
            self._is_paused = False
            self._pause_event.set()
            resumed_state = (
                self._previous_state if self._previous_state != AgentState.PAUSED else AgentState.EXECUTING
            )
            self._current_state = resumed_state
            self.on_state_change.emit(resumed_state)

    def stop(self) -> None:
        """Cancel and terminate the investigation loop."""
        self._is_stopped = True
        self._is_paused = False
        self._pause_event.set()  # Unblock any waiting coroutine so it can terminate
        self._current_state = AgentState.STOPPED
        self.on_state_change.emit(AgentState.STOPPED)
        if self._active_task is not None and not self._active_task.done():
            self._active_task.cancel()

    async def execute_terminal_command(self, cmd: str) -> tuple[int, str, str]:
        """Execute a shell command inside the Kali container or return error if offline.

        Runs asynchronously in a background thread via run_in_executor without blocking the UI.
        Returns a tuple of (exit_code, stdout, stderr).
        """
        cleaned = cmd.strip()
        if not cleaned:
            return 0, "", ""

        container = _find_or_start_kali_container()
        if container is not None and is_docker_available():
            loop = asyncio.get_running_loop()

            def _run() -> tuple[int, bytes, bytes]:
                res = container.exec_run(
                    cmd=["/bin/sh", "-c", cleaned],
                    user="bletiner",
                    workdir="/workspace",
                    stdout=True,
                    stderr=True,
                    demux=True,
                )
                ecode = int(getattr(res, "exit_code", res[0]))
                out = getattr(res, "output", res[1])
                out_b, err_b = out if isinstance(out, tuple) else (out, b"")
                return ecode, out_b or b"", err_b or b""

            try:
                ecode, out_b, err_b = await loop.run_in_executor(None, _run)
                stdout = out_b.decode("utf-8", "replace")
                stderr = err_b.decode("utf-8", "replace")
                return ecode, stdout, stderr
            except Exception as exc:
                return 1, "", f"Execution error: {exc}"

        return (
            1,
            "",
            "[Docker Sandbox Offline] Kali container 'my-kali' is not running. Please ensure Docker Desktop is started to run live commands.",
        )

    def submit_steering(self, instruction: str) -> SteeringResult:
        """Submit mid-turn steering instruction to the active agent loop."""
        if self._steering_queue is None:
            return SteeringResult(
                verdict=SteeringVerdict.REJECTED_SAFETY,
                instruction=instruction,
                reason="No active investigation steering queue",
            )
        result = self._steering_queue.submit(instruction)
        if not result.accepted:
            self.on_event.emit(
                AgentEvent(
                    EventType.STEERING_REJECTED,
                    payload={"instruction": instruction, "reason": result.reason},
                )
            )
        return result

    def _handle_event(self, event: AgentEvent) -> None:
        """Internal dispatch for agent loop events."""
        if event.type == EventType.STATE_CHANGED:
            state_val = str(event.payload.get("state", ""))
            try:
                state = AgentState(state_val)
                self._current_state = state
                self.on_state_change.emit(state)
            except ValueError:
                pass
        self.on_event.emit(event)

    def _handle_tool_output(self, tool_name: str, argv: list[str], output: str) -> None:
        """Internal dispatch for tool output."""
        self.on_tool_output.emit(tool_name, argv, output)

    async def start(
        self,
        target: str,
        mode: str = "deep",
        provider: str = "fake",
        api_key: str | None = None,
        model: str | None = None,
        *,
        step_delay_s: float = 0.25,
    ) -> AgentResult:
        """Execute an autonomous investigation against target asynchronously without blocking Qt."""
        self._is_stopped = False
        self._is_paused = False
        self._pause_event.set()
        self._current_state = AgentState.INITIALIZING
        self.on_state_change.emit(AgentState.INITIALIZING)

        cleaned_target = _clean_target(target)
        if not cleaned_target:
            raise ValueError("Target specification cannot be empty.")

        scope = _build_scope(cleaned_target)
        inv_mode = (
            InvestigationMode.DEEP
            if str(mode).lower() in ("deep", "investigationmode.deep")
            else InvestigationMode.BASIC
        )

        inv_id = new_id()
        session_id = new_id()
        store = ArtifactStore(self._app_data_dir)
        paths = store.create(inv_id)
        self._current_paths = paths

        self._steering_queue = SteeringQueue(scope)

        # Select provider adapter
        provider_adapter: ProviderAdapter
        model_str = f"{provider}-model"
        base_url: str | None = None

        if provider == "anthropic":
            if not api_key:
                raise ValueError("Anthropic API Key is required. Please configure it in Settings tab.")
            provider_adapter = AnthropicAdapter(HttpTransport(timeout_s=60.0), api_key=api_key)
            model_str = (
                "claude-3-opus-20240229"
                if model and "opus" in model.lower()
                else "claude-3-5-sonnet-20241022"
            )
        elif provider == "deepseek":
            if not api_key:
                raise ValueError("DeepSeek API Key is required. Please configure it in Settings tab.")
            provider_adapter = DeepSeekAdapter(HttpTransport(timeout_s=60.0), api_key=api_key)
            model_str = (
                "deepseek-reasoner"
                if model and "r1" in model.lower()
                else "deepseek-chat"
            )
            base_url = "https://api.deepseek.com"
        elif provider == "openai":
            if not api_key:
                raise ValueError("OpenAI API Key is required. Please configure it in Settings tab.")
            provider_adapter = OpenAICompatibleAdapter(HttpTransport(timeout_s=60.0), api_key=api_key)
            model_str = "gpt-4o"
        elif provider == "ollama":
            provider_adapter = OpenAICompatibleAdapter(HttpTransport(timeout_s=60.0), api_key=api_key or "ollama")
            model_str = "llama3"
            base_url = "http://localhost:11434/v1"
        else:
            # Deterministic simulation lab
            scripted = _build_scripted_responses(cleaned_target, inv_mode)
            provider_adapter = FakeProvider(scripted)
            model_str = "simulation-lab"

        # Executor selection
        base_executor: ToolExecutor
        if provider == "fake":
            base_executor = SimulatedToolExecutor(
                on_output=None,
                step_delay_s=step_delay_s,
            )
            use_live = False
        else:
            is_live_docker = is_docker_available() and is_gate3_certified(self._evidence_dir)
            container = _find_or_start_kali_container() if is_live_docker else None
            if container is not None and is_live_docker:
                base_executor = DockerToolExecutor(container)
                self._handle_tool_output(
                    "[SYSTEM]",
                    [],
                    f"[LIVE EXECUTION] Connected to Kali sandbox container '{getattr(container, 'name', 'kali')}'. Tools execute live inside container.",
                )
                use_live = True
            else:
                use_live = False
                self._handle_tool_output(
                    "[SYSTEM]",
                    [],
                    "[NOTICE] Kali sandbox container not attached. Running tools with simulated observation responses.",
                )
                base_executor = SimulatedToolExecutor(
                    on_output=None,
                    step_delay_s=step_delay_s,
                )

        notifying_executor = ToolExecutorNotifier(
            inner=base_executor,
            on_output=self._handle_tool_output,
            pause_event=self._pause_event,
            check_stopped=lambda: self._is_stopped,
        )

        config = AgentConfig(
            profile=ModelProfile(provider, model_str, base_url=base_url),
            mode=inv_mode,
            budget=LoopBudget(max_iterations=15, max_tokens=50_000),
        )

        request = InvestigationRequest(
            objective=f"Investigate target security posture for {cleaned_target}",
            scope=scope,
            config=config,
        )

        # Initialize InvestigationService
        self._service = InvestigationService(
            provider=provider_adapter,
            registry=self._registry,
            evidence_dir=self._evidence_dir if use_live else None,
            on_event=self._handle_event,
        )

        try:
            self._current_state = AgentState.INITIALIZING
            self.on_state_change.emit(AgentState.INITIALIZING)

            if use_live:
                result = await self._service.start(
                    request,
                    notifying_executor,
                    steering=self._steering_queue,
                )
            else:
                loop = AgentLoop(
                    provider=provider_adapter,
                    registry=self._registry,
                    executor=notifying_executor,
                    config=config,
                    steering=self._steering_queue,
                    on_event=self._handle_event,
                )
                result = await loop.run(request.objective)

            if self._is_stopped:
                self._current_state = AgentState.STOPPED
                self.on_state_change.emit(AgentState.STOPPED)
                return result

            # Build formal domain findings from loop results
            domain_findings: list[Finding] = []
            for rf in result.findings:
                sev_key = rf.severity.upper()
                sev = Severity[sev_key] if sev_key in Severity.__members__ else Severity.MEDIUM
                conf_key = rf.confidence.upper()
                conf = Confidence[conf_key] if conf_key in Confidence.__members__ else Confidence.HIGH

                port: int | None = None
                asset = rf.asset
                if ":" in asset:
                    parts = asset.split(":")
                    asset = parts[0]
                    try:
                        port = int(parts[1])
                    except ValueError:
                        port = None

                finding = Finding(
                    investigation_id=inv_id,
                    session_id=session_id,
                    stable_finding_key=hashlib.sha256(f"{rf.title}:{rf.asset}".encode()).hexdigest(),
                    title=rf.title,
                    summary=rf.summary,
                    category="Vulnerability",
                    affected_asset=asset,
                    affected_port=port,
                    severity=sev,
                    confidence=conf,
                    verification_status=VerificationStatus.VERIFIED,
                    detection_method="autonomous_scan",
                    is_included_in_report=True,
                )
                domain_findings.append(finding)

            # Assemble reports
            scope_entries = tuple(scope.domains) if scope.domains else tuple(t.cidr for t in scope.ip_targets)
            report_ctx = ReportContext(
                title=f"Investigation Report: {cleaned_target}",
                primary_target=cleaned_target,
                authorized_scope=scope_entries,
                findings=tuple(domain_findings),
            )

            md_content = build_markdown(report_ctx)
            html_content = build_html(report_ctx)

            (paths.reports / "report.md").write_text(md_content, encoding="utf-8")
            (paths.reports / "report.html").write_text(html_content, encoding="utf-8")

            # Write evidence and logs
            findings_data = [f.model_dump(mode="json") for f in domain_findings]
            (paths.evidence / "findings.json").write_text(
                json.dumps(findings_data, indent=2, default=str), encoding="utf-8"
            )

            events_data = [
                {"timestamp": ev.timestamp.isoformat(), "type": ev.type.value, "payload": ev.payload}
                for ev in result.events
            ]
            (paths.logs / "agent_runtime.log").write_text(
                json.dumps(events_data, indent=2, default=str), encoding="utf-8"
            )

            (paths.root / "session.json").write_text(
                json.dumps(
                    {
                        "investigation_id": inv_id,
                        "session_id": session_id,
                        "target": cleaned_target,
                        "mode": mode,
                        "stop_reason": result.stop_reason,
                        "iterations": result.iterations,
                        "tokens_used": result.tokens_used,
                        "findings_count": len(domain_findings),
                        "reports": [
                            str(paths.reports / "report.md"),
                            str(paths.reports / "report.html"),
                        ],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            self._last_result = result
            self._current_state = AgentState.COMPLETED
            self.on_state_change.emit(AgentState.COMPLETED)
            self.on_completed.emit(result, paths)
            return result

        except asyncio.CancelledError:
            self._current_state = AgentState.STOPPED
            self.on_state_change.emit(AgentState.STOPPED)
            raise
        except Exception as exc:
            self._current_state = AgentState.ERROR_HANDLING
            self.on_state_change.emit(AgentState.ERROR_HANDLING)
            self.on_error.emit(exc)
            raise
