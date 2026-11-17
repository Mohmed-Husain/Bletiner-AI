"""Controller tests: real local engine path with a fake Docker container (no Docker daemon needed)."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from bletiner.agent.events import AgentState
from bletiner.providers.base import AgentMessage, ProviderResponse, Role, Usage
from bletiner.ui import controller as controller_module
from bletiner.ui.controller import InvestigationController, _find_kali_container

NMAP_FAST = """Starting Nmap 7.99 ( https://nmap.org )
Nmap scan report for 127.0.0.1
Host is up (0.001s latency).
PORT     STATE SERVICE
22/tcp   open  ssh
80/tcp   open  http
443/tcp  open  https
Nmap done: 1 IP address (1 host up) scanned in 0.20 seconds
"""

NMAP_SERVICE = """PORT     STATE SERVICE  VERSION
22/tcp   open  ssh      OpenSSH 8.2p1 Ubuntu 4ubuntu0.5
80/tcp   open  http     Apache httpd 2.4.41 ((Ubuntu))
443/tcp  open  https    nginx 1.18.0 (Ubuntu)
"""

HTTP_HEAD = """HTTP/1.1 200 OK
Server: Apache/2.4.41 (Ubuntu)
X-Powered-By: PHP/7.4.3
Content-Type: text/html
"""

WHATWEB = "http://127.0.0.1 [200 OK] Apache[2.4.41], PHP[7.4.3], jQuery[1.12.4]"

NIKTO = """
+ Server: Apache/2.4.41 (Ubuntu)
+ The anti-clickjacking X-Frame-Options header is not present.
+ 1 host(s) tested
"""

DIG = ";; ANSWER SECTION:\n127.0.0.1. 0 IN A 127.0.0.1\n"
WHOIS = "Domain Name: EXAMPLE.COM\nRegistrar: Test\n"


class FakeExecResult:
    def __init__(self, exit_code: int, output: tuple[bytes, bytes]) -> None:
        self.exit_code = exit_code
        self.output = output


class FakeContainer:
    id = "fake-kali"
    name = "my-kali"
    status = "running"

    def exec_run(self, cmd=None, user=None, stdout=True, stderr=True, demux=True, **kwargs):
        joined = " ".join(str(part) for part in (cmd or []))
        text = "ok\n"
        if "nmap" in joined and " -F" in joined:
            text = NMAP_FAST
        elif "nmap" in joined:
            text = NMAP_SERVICE
        elif "curl" in joined:
            text = HTTP_HEAD
        elif "whatweb" in joined:
            text = WHATWEB
        elif "nikto" in joined:
            text = NIKTO
        elif "dig" in joined:
            text = DIG
        elif "whois" in joined:
            text = WHOIS
        return FakeExecResult(0, (text.encode(), b""))


class SlowProvider:
    """Fake provider whose blocking call exercises the worker-thread + pause/stop gates."""

    name = "stub"

    def __init__(self, delay: float = 0.4) -> None:
        self.delay = delay
        self.calls = 0

    def complete(self, profile, messages, *, tools=None, api_key=None):
        self.calls += 1
        time.sleep(self.delay)
        return ProviderResponse(AgentMessage(Role.ASSISTANT, "analysis complete"), Usage(2, 3))


def certify_gate(evidence_dir: Path) -> None:
    """Write a genuine-looking passing live gate report so the AI path's guard is satisfied in-test."""
    evidence_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": "2026-01-01T00:00:00+00:00",
        "mode": "live",
        "gate_passed": True,
        "totals": {"total": 1, "pass": 1, "fail": 0, "not_run": 0},
        "results": [],
    }
    (evidence_dir / "gate3_live_test.json").write_text(json.dumps(report), encoding="utf-8")


class FakeSettings:
    def __init__(self, *, key: str = "", validation: tuple[bool, str] = (True, "Key accepted - ok")) -> None:
        self.settings = SimpleNamespace(
            active_provider="deepseek",
            active_model="deepseek-chat",
            authorized_scope=["127.0.0.1", "example.com"],
            ollama_base_url="http://localhost:11434/v1",
        )
        self.secrets = SimpleNamespace(backend="Windows DPAPI")
        self._key = key
        self._validation = validation
        self.validated: list[tuple[str, str]] = []

    def get_api_key(self, provider: str) -> str:
        return self._key

    def validate_api_key(self, provider: str, key: str) -> tuple[bool, str]:
        self.validated.append((provider, key))
        return self._validation


_OPEN_CONTROLLERS: list[InvestigationController] = []


@pytest.fixture(autouse=True)
async def _close_controllers() -> None:
    yield
    for controller in _OPEN_CONTROLLERS:
        if controller._store is not None:
            await controller._store.close()
            controller._store = None
    _OPEN_CONTROLLERS.clear()


def make_controller(tmp_path: Path, fake_settings: FakeSettings | None = None) -> InvestigationController:
    controller_module.get_settings_manager = lambda: fake_settings or FakeSettings()  # type: ignore[assignment]
    controller = InvestigationController(
        app_data_dir=tmp_path / "data",
        evidence_dir=tmp_path / "evidence",
        monitor_readiness=False,
    )
    _OPEN_CONTROLLERS.append(controller)
    return controller


class _FakeContainers:
    def __init__(self, containers: list[object], images: list[object]) -> None:
        self._containers = containers
        self._images = images
        self.created: list[dict[str, object]] = []

    def list(self, all: bool = False) -> list[object]:
        if all:
            return list(self._containers)
        return [c for c in self._containers if getattr(c, "status", "") == "running"]

    def run(self, image: str, **kwargs: object) -> object:
        self.created.append({"image": image, **kwargs})
        container = SimpleNamespace(name="my-kali", status="running")
        self._containers.append(container)
        return container

    def get(self, name: str) -> object:
        raise KeyError(name)


class _FakeDocker:
    def __init__(self, containers: list[object], images: list[object]) -> None:
        self.containers = _FakeContainers(containers, images)
        self.images = SimpleNamespace(list=lambda: list(images))


def test_find_kali_container_autoprovisions_from_local_image() -> None:
    image = SimpleNamespace(tags=["bletiner-kali:latest"])
    client = _FakeDocker(containers=[], images=[image])
    container = _find_kali_container(client)
    assert container is not None
    assert client.containers.created
    assert client.containers.created[0]["image"] == "bletiner-kali:latest"


def test_find_kali_container_starts_stopped_container() -> None:
    started: list[bool] = []

    class Stopped:
        name = "my-kali"
        status = "exited"
        image = SimpleNamespace(tags=[])

        def start(self) -> None:
            started.append(True)

    client = _FakeDocker(containers=[Stopped()], images=[])
    container = _find_kali_container(client)
    assert container is not None
    assert started == [True]


def test_validate_target() -> None:
    controller = InvestigationController(monitor_readiness=False)
    assert controller.validate_target("https://example.com/path?q=1") == ("example.com", None)
    assert controller.validate_target("Example.COM.") == ("example.com", None)
    assert controller.validate_target("10.0.0.5") == ("10.0.0.5", None)
    assert controller.validate_target("example.com:8443") == ("example.com", None)
    cleaned, error = controller.validate_target("not a host")
    assert cleaned is None and error is not None
    cleaned, error = controller.validate_target("*.example.com")
    assert cleaned is None and error is not None


def test_target_scope_matching() -> None:
    controller = InvestigationController(monitor_readiness=False)
    authorized = ["example.com", "10.0.0.0/24", "127.0.0.1"]
    assert controller.target_in_authorized_scope("example.com", authorized)
    assert controller.target_in_authorized_scope("api.example.com", authorized)
    assert controller.target_in_authorized_scope("10.0.0.42", authorized)
    assert controller.target_in_authorized_scope("127.0.0.1", authorized)
    assert not controller.target_in_authorized_scope("evil.example.net", authorized)
    assert not controller.target_in_authorized_scope("11.0.0.42", authorized)


def test_readiness_is_honest_when_everything_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(controller_module, "_client_or_none", lambda: None)
    fake_settings = FakeSettings()
    controller_module.get_settings_manager = lambda: fake_settings  # type: ignore[assignment]
    controller = InvestigationController(
        app_data_dir=tmp_path / "data", evidence_dir=tmp_path / "evidence", monitor_readiness=False
    )
    readiness = controller._probe_readiness()
    assert readiness.docker_running is False
    assert readiness.kali_container is None
    assert readiness.local_ready is False
    assert readiness.ai_ready is False
    assert any("Docker" in note for note in readiness.notes)


@pytest.mark.asyncio
async def test_start_rejects_invalid_target(tmp_path: Path) -> None:
    controller = make_controller(tmp_path)
    with pytest.raises(ValueError, match="single hostname"):
        await controller.start(target="not a host", mode="basic")


@pytest.mark.asyncio
async def test_start_rejects_out_of_scope_target(tmp_path: Path) -> None:
    controller = make_controller(tmp_path)
    with pytest.raises(ValueError, match="outside the authorized scope"):
        await controller.start(target="evil.example.net", mode="basic")


@pytest.mark.asyncio
async def test_ai_start_requires_api_key(tmp_path: Path) -> None:
    controller = make_controller(tmp_path, FakeSettings(key=""))
    with pytest.raises(ValueError, match="No API key configured"):
        await controller.start(
            target="127.0.0.1", mode="basic", engine="ai", provider="deepseek", model="deepseek-chat"
        )


@pytest.mark.asyncio
async def test_ai_start_rejects_a_rejected_key_before_any_run(tmp_path: Path) -> None:
    settings = FakeSettings(key="sk-invalid", validation=(False, "Authentication failed (HTTP 401)"))
    controller = make_controller(tmp_path, settings)
    with pytest.raises(ValueError, match="rejected the API key"):
        await controller.start(
            target="127.0.0.1",
            mode="basic",
            engine="ai",
            provider="deepseek",
            model="deepseek-chat",
        )
    assert settings.validated == [("deepseek", "sk-invalid")]
    # The preflight runs before the session store and the sandbox are touched.
    assert controller.current_paths is None
    assert controller._store is None
    assert controller.last_outcome is None


@pytest.mark.asyncio
async def test_preflight_helper_reports_real_result(tmp_path: Path) -> None:
    settings = FakeSettings(key="sk-valid")
    controller = make_controller(tmp_path, settings)
    ok, detail = await controller._preflight_api_key("deepseek", "sk-valid")
    assert ok is True
    assert "Key accepted" in detail


@pytest.mark.asyncio
async def test_start_fails_cleanly_without_sandbox(tmp_path: Path) -> None:
    controller = make_controller(tmp_path)
    controller.kali_container = lambda: None  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="Kali sandbox is not available"):
        await controller.start(target="127.0.0.1", mode="basic", engine="local")


@pytest.mark.asyncio
async def test_local_engine_end_to_end_writes_real_artifacts(tmp_path: Path) -> None:
    controller = make_controller(tmp_path)
    fake_container = FakeContainer()
    controller.kali_container = lambda: fake_container  # type: ignore[method-assign]
    completed: list[object] = []
    states: list[AgentState] = []
    tool_outputs: list[tuple[str, list[str], str]] = []
    controller.on_completed.connect(completed.append)
    controller.on_state_change.connect(states.append)
    controller.on_tool_output.connect(lambda t, a, o: tool_outputs.append((t, a, o)))

    outcome = await controller.start(target="127.0.0.1", mode="deep", engine="local")

    assert outcome.status == "completed"
    assert outcome.engine == "local"
    assert outcome.target == "127.0.0.1"
    assert outcome.findings
    assert outcome.reports is not None
    report_md, report_html = outcome.reports
    assert report_md.exists() and "127.0.0.1" in report_md.read_text(encoding="utf-8")
    assert report_html.exists() and "<html" in report_html.read_text(encoding="utf-8")

    paths = outcome.paths
    session = json.loads((paths.root / "session.json").read_text(encoding="utf-8"))
    assert session["target"] == "127.0.0.1"
    assert session["status"] == "completed"
    assert session["engine"] == "local"
    assert session["findings_count"] == len(outcome.findings)

    findings_json = json.loads((paths.evidence / "findings.json").read_text(encoding="utf-8"))
    assert len(findings_json) == len(outcome.findings)
    manifest = json.loads((paths.evidence / "manifest.json").read_text(encoding="utf-8"))
    assert manifest and all("sha256" in item for item in manifest)
    assert (paths.logs / "agent_runtime.json").exists()
    assert completed and completed[0] is outcome
    assert AgentState.INITIALIZING in states
    assert AgentState.COMPLETED in states
    assert tool_outputs
    assert controller.current_findings
    assert controller.last_outcome is outcome

    sessions = await controller.sessions()
    assert any(summary.investigation_id == outcome.investigation_id for summary in sessions)
    loaded = await controller.findings_for(outcome.investigation_id)
    assert len(loaded) == len(outcome.findings)


@pytest.mark.asyncio
async def test_ai_engine_can_be_paused_and_resumed(tmp_path: Path) -> None:
    settings = FakeSettings(key="sk-valid")
    controller = make_controller(tmp_path, settings)
    certify_gate(tmp_path / "evidence")
    controller.kali_container = FakeContainer  # type: ignore[method-assign]
    provider = SlowProvider(delay=0.4)
    controller._build_provider = lambda *args, **kwargs: provider  # type: ignore[method-assign]

    task = asyncio.create_task(
        controller.start(
            target="example.com", mode="basic", engine="ai", provider="deepseek", model="deepseek-chat"
        )
    )
    await asyncio.sleep(0.15)
    assert controller.is_running
    assert controller.pause() is True
    assert controller.current_state is AgentState.PAUSED
    await asyncio.sleep(0.5)  # longer than the provider delay: the loop must hold at the pause gate
    assert not task.done()
    controller.resume()
    outcome = await asyncio.wait_for(task, timeout=15)
    assert outcome.status == "completed"
    assert outcome.engine == "ai"
    assert provider.calls >= 1


@pytest.mark.asyncio
async def test_ai_engine_stop_finalises_a_stopped_session(tmp_path: Path) -> None:
    settings = FakeSettings(key="sk-valid")
    controller = make_controller(tmp_path, settings)
    certify_gate(tmp_path / "evidence")
    controller.kali_container = FakeContainer  # type: ignore[method-assign]
    controller._build_provider = lambda *args, **kwargs: SlowProvider(delay=0.4)  # type: ignore[method-assign]

    task = asyncio.create_task(
        controller.start(
            target="example.com", mode="basic", engine="ai", provider="deepseek", model="deepseek-chat"
        )
    )
    await asyncio.sleep(0.15)
    assert controller.is_running
    controller.stop()
    outcome = await asyncio.wait_for(task, timeout=15)
    assert outcome.status == "stopped"
    assert outcome.stop_reason == "stopped_by_operator"


@pytest.mark.asyncio
async def test_pause_resume_and_stop_states(tmp_path: Path) -> None:
    controller = make_controller(tmp_path)
    controller._current_state = AgentState.EXECUTING
    assert controller.pause() is True
    assert controller.current_state is AgentState.PAUSED
    assert controller.pause() is False
    assert controller.current_state is AgentState.EXECUTING
    controller.stop()
    assert controller.current_state is AgentState.STOPPED


@pytest.mark.asyncio
async def test_terminal_offline_is_explicit(tmp_path: Path) -> None:
    controller = make_controller(tmp_path)
    controller.kali_container = lambda: None  # type: ignore[method-assign]
    code, _stdout, stderr = await controller.execute_terminal_command("id")
    assert code != 0
    assert "Sandbox offline" in stderr


@pytest.mark.asyncio
async def test_delete_session_removes_investigation(tmp_path: Path) -> None:
    controller = make_controller(tmp_path)
    controller.kali_container = FakeContainer  # type: ignore[method-assign]
    outcome = await controller.start(target="127.0.0.1", mode="basic", engine="local")
    root = outcome.paths.root
    assert root.exists()
    await controller.delete_session(outcome.investigation_id)
    assert not root.exists()
