"""Deterministic local reconnaissance engine (no LLM).

Runs REAL security tools inside the running Kali sandbox through an injected ToolExecutor, parses their
real output with pure functions, and returns honest findings. This is the always-available engine of the
desktop app: it needs Docker + the Kali container, but no API key and no model.

Nothing here simulates: every command is real, every finding is derived from captured output, and a failed
tool is reported as failed rather than replaced by canned data.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from bletiner.agent.events import AgentEvent, AgentState, EventType
from bletiner.agent.loop import ToolExecutor, ToolOutcome
from bletiner.tools.registry import InvestigationMode, ToolRegistry

_MAX_OUTPUT = 300_000
_MAX_FINDINGS = 40


@dataclass(frozen=True)
class DetectedFinding:
    """A finding derived from real tool output, before domain conversion."""

    title: str
    severity: str
    confidence: str
    asset: str
    summary: str
    category: str = "Vulnerability"
    evidence: str = ""
    remediation: str = ""
    verification: str = "VERIFIED"


@dataclass
class ReconResult:
    findings: list[DetectedFinding]
    stop_reason: str
    iterations: int
    events: list[AgentEvent]
    raw_outputs: dict[str, str]
    duration_s: float
    notes: list[str] = field(default_factory=list)


def _clean(text: str) -> str:
    return " ".join(text.split())[:240]


# ---------------------------------------------------------------------------------------------------------
# Pure parsers / analyzers (unit tested without Docker)
# ---------------------------------------------------------------------------------------------------------
_NMAP_PORT_RE = re.compile(r"^(\d{1,5})/(tcp|udp)\s+(\w+)\s+(\S+)?\s*(.*)$")


@dataclass(frozen=True)
class NmapPort:
    port: int
    protocol: str
    state: str
    service: str
    version: str


def parse_nmap_ports(output: str) -> list[NmapPort]:
    ports: list[NmapPort] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        m = _NMAP_PORT_RE.match(line)
        if not m:
            continue
        port = int(m.group(1))
        if not (0 < port <= 65535):
            continue
        state = m.group(3).lower()
        if state not in ("open", "open|filtered"):
            continue
        service = (m.group(4) or "unknown").strip()
        version = (m.group(5) or "").strip()
        ports.append(NmapPort(port, m.group(2), state, service, version))
    return ports


_APACHE_RE = re.compile(r"Apache(?: httpd)?[/ ]?(\d+)\.(\d+)\.(\d+)", re.IGNORECASE)
_OPENSSH_RE = re.compile(r"OpenSSH[_ ](\d+)\.(\d+)", re.IGNORECASE)
_NGINX_RE = re.compile(r"nginx[/ ](\d+)\.(\d+)\.(\d+)", re.IGNORECASE)
_PHP_RE = re.compile(r"PHP[/ ](\d+)\.(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class _ServiceRisk:
    severity: str
    title: str
    summary: str
    remediation: str


_RISKY_PORTS: dict[int, _ServiceRisk] = {
    21: _ServiceRisk(
        "MEDIUM",
        "FTP service exposed",
        "An FTP service is reachable. FTP is cleartext and frequently allows weak or anonymous authentication.",
        "Disable FTP or replace it with SFTP/FTPS and restrict access by source address.",
    ),
    23: _ServiceRisk(
        "HIGH",
        "Telnet service exposed",
        "Telnet transmits credentials and session data in cleartext.",
        "Disable Telnet and use SSH instead.",
    ),
    111: _ServiceRisk(
        "LOW",
        "rpcbind service exposed",
        "rpcbind (portmapper) is reachable and can disclose registered RPC services.",
        "Restrict rpcbind to trusted networks or disable it when unused.",
    ),
    445: _ServiceRisk(
        "HIGH",
        "SMB service exposed",
        "Server Message Block is reachable. SMB has a history of critical remotely exploitable flaws.",
        "Block SMB at the network edge and patch the file-sharing service.",
    ),
    2375: _ServiceRisk(
        "CRITICAL",
        "Docker API exposed without TLS",
        "The Docker Engine API is reachable on its plaintext port and grants host-level control to anyone who can reach it.",
        "Never expose the Docker API; bind it to a local socket or protect it with mutual TLS.",
    ),
    3306: _ServiceRisk(
        "MEDIUM",
        "MySQL service exposed",
        "A MySQL database port is reachable from the network.",
        "Bind the database to localhost or a private network and restrict access to known application hosts.",
    ),
    3389: _ServiceRisk(
        "MEDIUM",
        "RDP service exposed",
        "Remote Desktop is reachable from the network and is a common credential-attack target.",
        "Require VPN or a bastion host for RDP and enforce MFA.",
    ),
    5432: _ServiceRisk(
        "MEDIUM",
        "PostgreSQL service exposed",
        "A PostgreSQL database port is reachable from the network.",
        "Bind the database to a private interface and restrict access with host-based rules.",
    ),
    5900: _ServiceRisk(
        "HIGH",
        "VNC service exposed",
        "A VNC remote-desktop port is reachable and is often protected only by a weak password.",
        "Tunnel VNC over SSH/VPN or disable it when unused.",
    ),
    6379: _ServiceRisk(
        "HIGH",
        "Redis service exposed",
        "Redis is reachable from the network. Unauthenticated Redis commonly allows data theft and remote command execution.",
        "Require authentication, bind to localhost, and never expose Redis to untrusted networks.",
    ),
    9200: _ServiceRisk(
        "HIGH",
        "Elasticsearch HTTP API exposed",
        "The Elasticsearch API is reachable; many deployments have no authentication enabled.",
        "Enable authentication and TLS and restrict the API to trusted hosts.",
    ),
    11211: _ServiceRisk(
        "HIGH",
        "Memcached service exposed",
        "Memcached is reachable and can be abused for data theft or amplification attacks.",
        "Bind Memcached to localhost and disable UDP.",
    ),
    27017: _ServiceRisk(
        "HIGH",
        "MongoDB service exposed",
        "MongoDB is reachable from the network and has historically shipped without authentication.",
        "Enable authentication, bind to a private interface, and restrict access.",
    ),
}


def _port_finding(port: NmapPort, target: str) -> DetectedFinding | None:
    risk = _RISKY_PORTS.get(port.port)
    if risk is None:
        return None
    asset = f"{target}:{port.port}"
    evidence = f"{port.port}/{port.protocol} {port.state} {port.service} {port.version}".strip()
    return DetectedFinding(
        title=risk.title,
        severity=risk.severity,
        confidence="HIGH",
        asset=asset,
        summary=risk.summary,
        category="Exposure",
        evidence=evidence,
        remediation=risk.remediation,
        verification="VERIFIED",
    )


def _outdated_version_findings(service_text: str, target: str, port: int) -> list[DetectedFinding]:
    findings: list[DetectedFinding] = []
    asset = f"{target}:{port}"
    apache = _APACHE_RE.search(service_text)
    if apache:
        major, minor, patch = (int(apache.group(i)) for i in (1, 2, 3))
        if (major, minor) == (2, 4) and patch < 59:
            findings.append(
                DetectedFinding(
                    title="Outdated Apache HTTP Server",
                    severity="MEDIUM",
                    confidence="HIGH",
                    asset=asset,
                    summary=(
                        f"The service banner reports Apache httpd {major}.{minor}.{patch}, an older 2.4.x "
                        "release. Confirm the exact build against current Apache security advisories before "
                        "treating this as exploitable."
                    ),
                    category="Vulnerability",
                    evidence=f"Apache httpd {major}.{minor}.{patch}",
                    remediation="Upgrade Apache httpd to a currently supported 2.4.x release.",
                )
            )
    ssh = _OPENSSH_RE.search(service_text)
    if ssh:
        major, minor = int(ssh.group(1)), int(ssh.group(2))
        if major < 9:
            findings.append(
                DetectedFinding(
                    title="Outdated OpenSSH Server",
                    severity="LOW",
                    confidence="HIGH",
                    asset=asset,
                    summary=(
                        f"The SSH banner reports OpenSSH {major}.{minor}, older than the 9.x series. Older "
                        "releases may miss hardening fixes; verify the distribution backport level."
                    ),
                    category="Vulnerability",
                    evidence=f"OpenSSH {major}.{minor}",
                    remediation="Upgrade OpenSSH to the current release supported by the operating system.",
                )
            )
    nginx = _NGINX_RE.search(service_text)
    if nginx:
        major, minor = int(nginx.group(1)), int(nginx.group(2))
        if (major, minor) < (1, 24):
            findings.append(
                DetectedFinding(
                    title="Outdated nginx Server",
                    severity="LOW",
                    confidence="HIGH",
                    asset=asset,
                    summary=f"The server banner reports nginx {major}.{minor}, older than the 1.24 stable line.",
                    category="Vulnerability",
                    evidence=f"nginx {major}.{minor}",
                    remediation="Upgrade nginx to a supported stable release.",
                )
            )
    php = _PHP_RE.search(service_text)
    if php:
        major, minor = int(php.group(1)), int(php.group(2))
        if major < 8 or (major == 8 and minor < 1):
            findings.append(
                DetectedFinding(
                    title="Outdated PHP Runtime",
                    severity="MEDIUM",
                    confidence="HIGH",
                    asset=asset,
                    summary=f"The banner reports PHP {major}.{minor}; PHP 7.x and early 8.0 releases are end-of-life.",
                    category="Vulnerability",
                    evidence=f"PHP {major}.{minor}",
                    remediation="Upgrade PHP to a supported 8.2+ release.",
                )
            )
    return findings


def analyze_nmap(output: str, target: str) -> list[DetectedFinding]:
    """Turn real nmap output into exposure/version findings."""
    ports = parse_nmap_ports(output)
    findings: list[DetectedFinding] = []
    for port in ports:
        risk = _port_finding(port, target)
        if risk is not None:
            findings.append(risk)
        findings.extend(_outdated_version_findings(port.version or port.service, target, port.port))
    return findings


def parse_http_headers(output: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in output.replace("\r\n", "\n").splitlines():
        if ":" not in line:
            continue
        name, _, value = line.partition(":")
        key = name.strip().lower()
        if key and not key.startswith("http/"):
            headers[key] = value.strip()
    return headers


def analyze_http_headers(output: str, target: str, *, url: str) -> list[DetectedFinding]:
    """Check real HTTP response headers for missing protections and banner disclosure."""
    headers = parse_http_headers(output)
    if not headers:
        return []
    findings: list[DetectedFinding] = []
    wanted = {
        "x-content-type-options": "X-Content-Type-Options",
        "x-frame-options": "X-Frame-Options",
        "content-security-policy": "Content-Security-Policy",
    }
    missing = [pretty for key, pretty in wanted.items() if key not in headers]
    if missing:
        findings.append(
            DetectedFinding(
                title="Missing HTTP security headers",
                severity="LOW",
                confidence="CONFIRMED",
                asset=url,
                summary="The HTTP response does not set: "
                + ", ".join(missing)
                + ". These headers reduce clickjacking, MIME-sniffing and injection impact.",
                category="Web",
                evidence=", ".join(missing),
                remediation="Add the missing response headers at the web server or application layer.",
            )
        )
    if url.startswith("https://") and "strict-transport-security" not in headers:
        findings.append(
            DetectedFinding(
                title="Missing HSTS header on HTTPS",
                severity="MEDIUM",
                confidence="CONFIRMED",
                asset=url,
                summary="HTTPS responses do not send Strict-Transport-Security, so downgrade attacks are not mitigated for this host.",
                category="Web",
                evidence="Strict-Transport-Security absent",
                remediation="Send Strict-Transport-Security with a suitable max-age for HTTPS responses.",
            )
        )
    server = headers.get("server", "")
    if re.search(r"\d+\.\d+", server):
        findings.append(
            DetectedFinding(
                title="Web server version disclosed",
                severity="LOW",
                confidence="CONFIRMED",
                asset=url,
                summary=f"The Server response header discloses the software version: {_clean(server)}. This helps attackers select matching exploits.",
                category="Information Disclosure",
                evidence=server[:160],
                remediation="Suppress detailed version tokens in server banners.",
            )
        )
    powered = headers.get("x-powered-by", "")
    if powered:
        findings.append(
            DetectedFinding(
                title="X-Powered-By header disclosed",
                severity="INFO",
                confidence="CONFIRMED",
                asset=url,
                summary=f"The response advertises backend technology: {_clean(powered)}.",
                category="Information Disclosure",
                evidence=powered[:160],
                remediation="Remove the X-Powered-By header.",
            )
        )
    cookies = headers.get("set-cookie", "")
    if cookies and ("secure" not in cookies.lower() or "httponly" not in cookies.lower()):
        findings.append(
            DetectedFinding(
                title="Cookie missing Secure/HttpOnly flags",
                severity="LOW",
                confidence="CONFIRMED",
                asset=url,
                summary="A Set-Cookie response header lacks the Secure and/or HttpOnly flag.",
                category="Web",
                evidence=cookies[:200],
                remediation="Mark session cookies Secure and HttpOnly.",
            )
        )
    return findings


_WHATWEB_PATTERNS: dict[str, tuple[str, str, str]] = {
    "Apache": ("Apache HTTP Server", "MEDIUM", "Update Apache httpd to a supported release."),
    "PHP": ("PHP runtime", "MEDIUM", "Run a supported PHP release."),
    "jQuery": ("jQuery library", "LOW", "Upgrade jQuery to the current 3.x release."),
    "WordPress": ("WordPress", "MEDIUM", "Keep WordPress core and plugins updated."),
}


def analyze_whatweb(output: str, target: str, *, url: str) -> list[DetectedFinding]:
    findings: list[DetectedFinding] = []
    for name, (title, severity, remediation) in _WHATWEB_PATTERNS.items():
        m = re.search(re.escape(name) + r"\[([^\]]+)\]", output, re.IGNORECASE)
        if not m:
            continue
        version = m.group(1).strip()
        if not re.search(r"\d", version):
            continue
        old = False
        numbers = [int(x) for x in re.findall(r"\d+", version)[:3]]
        if (
            name == "Apache"
            and len(numbers) >= 2
            and (numbers[0], numbers[1]) <= (2, 4)
            and (len(numbers) < 3 or numbers[2] < 59)
        ):
            old = True
        if (
            name == "PHP"
            and numbers
            and (numbers[0] < 8 or (numbers[0] == 8 and len(numbers) > 1 and numbers[1] < 1))
        ):
            old = True
        if name == "jQuery" and numbers and numbers[0] < 3:
            old = True
        if not old:
            continue
        findings.append(
            DetectedFinding(
                title=f"Outdated {title} detected",
                severity=severity,
                confidence="HIGH",
                asset=url,
                summary=f"Technology fingerprint reports {name} {version}, which is below the current supported line.",
                category="Vulnerability",
                evidence=f"{name}[{version}]",
                remediation=remediation,
            )
        )
    return findings


_NIKTO_SKIP = (
    "target ip",
    "target hostname",
    "target port",
    "requests:",
    "host(s) tested",
    "server:",
    "platform:",
    "start time",
    "end time",
    "no cgi",
    "suggested security header missing",  # already aggregated by the HTTP header check
)


def analyze_nikto(output: str, target: str, *, url: str) -> list[DetectedFinding]:
    findings: list[DetectedFinding] = []
    seen: set[str] = set()
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line.startswith("+"):
            continue
        item = line.lstrip("+").strip()
        low = item.lower()
        if not item or any(skip in low for skip in _NIKTO_SKIP):
            continue
        key = item[:120].lower()
        if key in seen:
            continue
        seen.add(key)
        severity = "INFO"
        if (
            "sql" in low
            or "injection" in low
            or "xss" in low
            or "cross site" in low
            or "script" in low
            or "outdated" in low
            or "osvdb-3092" in low
            or "vulnerable" in low
        ):
            severity = "MEDIUM"
        elif "header" in low or "x-frame" in low or "x-content" in low:
            severity = "LOW"
        findings.append(
            DetectedFinding(
                title="Nikto: " + item[:110],
                severity=severity,
                confidence="MEDIUM",
                asset=url,
                summary="Nikto reported this item against the live web service. Treat it as a lead to verify manually.",
                category="Web",
                evidence=item[:300],
                remediation="Review the reported issue and apply the vendor or hardening guidance.",
                verification="NEEDS_TRIAGE",
            )
        )
        if len(findings) >= 8:
            break
    return findings


_NUCLEI_RE = re.compile(r"^\[([^\]]+)\]\s+\[([^\]]+)\]\s+\[([^\]]+)\]\s+(.*)$")


def analyze_nuclei(output: str, target: str) -> list[DetectedFinding]:
    order = {"critical": "CRITICAL", "high": "HIGH", "medium": "MEDIUM", "low": "LOW", "info": "INFO"}
    findings: list[DetectedFinding] = []
    for raw_line in output.splitlines():
        m = _NUCLEI_RE.match(raw_line.strip())
        if not m:
            continue
        template, _kind, sev_raw, detail = m.groups()
        severity = order.get(sev_raw.strip().lower(), "INFO")
        asset = detail.strip()[:300] if detail.strip().startswith("http") else target
        findings.append(
            DetectedFinding(
                title=f"Nuclei: {template}",
                severity=severity,
                confidence="HIGH",
                asset=asset,
                summary=f"Nuclei template '{template}' matched. Severity reported by the template: {sev_raw}.",
                category="Vulnerability",
                evidence=raw_line.strip()[:400],
                remediation="Validate the match manually and apply the referenced vendor advisory.",
            )
        )
        if len(findings) >= 10:
            break
    return findings


def dedupe_findings(findings: list[DetectedFinding]) -> list[DetectedFinding]:
    """Stable de-duplication by (title, asset); keep the strongest severity."""
    rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
    chosen: dict[tuple[str, str], DetectedFinding] = {}
    for finding in findings:
        key = (finding.title.strip().lower(), finding.asset.strip().lower())
        current = chosen.get(key)
        if current is None or rank.get(finding.severity, 5) < rank.get(current.severity, 5):
            chosen[key] = finding
    return list(chosen.values())


# ---------------------------------------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------------------------------------
class ReconEngine:
    """Runs a real, non-destructive recon playbook against one authorized target."""

    def __init__(
        self,
        executor: ToolExecutor,
        registry: ToolRegistry,
        *,
        on_event: Callable[[AgentEvent], None] | None = None,
        on_tool_output: Callable[[str, list[str], str], None] | None = None,
        pause_event: asyncio.Event | None = None,
        check_stopped: Callable[[], bool] | None = None,
    ) -> None:
        self._executor = executor
        self._registry = registry
        self._on_event = on_event
        self._on_tool_output = on_tool_output
        self._pause_event = pause_event
        self._check_stopped = check_stopped
        self._events: list[AgentEvent] = []

    def _emit(self, etype: EventType, **payload: object) -> None:
        event = AgentEvent(etype, payload=dict(payload))
        self._events.append(event)
        if self._on_event is not None:
            self._on_event(event)

    def _state(self, state: AgentState) -> None:
        self._emit(EventType.STATE_CHANGED, state=state.value)

    async def _gate(self) -> None:
        if self._check_stopped is not None and self._check_stopped():
            raise asyncio.CancelledError("investigation stopped by operator")
        if self._pause_event is not None:
            await self._pause_event.wait()
        if self._check_stopped is not None and self._check_stopped():
            raise asyncio.CancelledError("investigation stopped by operator")

    def _tool_argv(self, tool_name: str, target_value: str, mode: InvestigationMode) -> list[str]:
        tool = self._registry.assert_schedulable(tool_name, mode)
        return tool.render_argv({"target": target_value})

    async def run(self, target: str, *, mode: str = "deep", objective: str = "") -> ReconResult:
        started = time.monotonic()
        inv_mode = InvestigationMode.DEEP if str(mode).lower().startswith("deep") else InvestigationMode.BASIC
        target = target.strip()
        findings: list[DetectedFinding] = []
        raw_outputs: dict[str, str] = {}
        notes: list[str] = []
        iterations = 0
        stop_reason = "completed"

        self._state(AgentState.INITIALIZING)
        if self._on_tool_output is not None:
            self._on_tool_output(
                "[SYSTEM]",
                [],
                f"Local recon engine starting against {target} (mode={inv_mode.name.lower()}). "
                "Real tools execute inside the Kali sandbox; no AI model is used.",
            )

        self._state(AgentState.PLANNING)
        domain_mode = "." in target and not re.fullmatch(r"[\d.]+", target)
        web_urls: list[str] = []

        if domain_mode:
            await self._gate()
            iterations += 1
            await self._execute("dig", target, raw_outputs, notes)

        await self._gate()
        self._state(AgentState.EXECUTING)
        iterations += 1
        await self._execute("nmap_fast", target, raw_outputs, notes)
        ports = parse_nmap_ports(raw_outputs.get("nmap_fast", ""))
        self._state(AgentState.ANALYZING)
        findings.extend(analyze_nmap(raw_outputs.get("nmap_fast", ""), target))
        web_ports = [p.port for p in ports if p.port in (80, 443, 8080, 8000, 8443, 3000, 5000)]
        for port in web_ports[:2]:
            scheme = "https" if port in (443, 8443) else "http"
            default = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
            host = target[:-1] if target.endswith("/") else target
            web_urls.append(f"{scheme}://{host}" + ("" if default else ":" + str(port)))

        if inv_mode is InvestigationMode.DEEP or not ports:
            await self._gate()
            self._state(AgentState.EXECUTING)
            iterations += 1
            await self._execute("nmap_service", target, raw_outputs, notes)
            self._state(AgentState.ANALYZING)
            findings.extend(analyze_nmap(raw_outputs.get("nmap_service", ""), target))

        if web_urls:
            url = web_urls[0]
            await self._gate()
            self._state(AgentState.EXECUTING)
            iterations += 1
            await self._execute("http_head", url, raw_outputs, notes)
            self._state(AgentState.ANALYZING)
            findings.extend(analyze_http_headers(raw_outputs.get("http_head", ""), target, url=url))

            await self._gate()
            self._state(AgentState.EXECUTING)
            iterations += 1
            await self._execute("whatweb", url, raw_outputs, notes)
            self._state(AgentState.ANALYZING)
            findings.extend(analyze_whatweb(raw_outputs.get("whatweb", ""), target, url=url))

            await self._gate()
            self._state(AgentState.EXECUTING)
            iterations += 1
            nikto_target = url.split("://", 1)[-1]
            await self._execute("nikto", nikto_target, raw_outputs, notes, timeout_s=600.0)
            self._state(AgentState.ANALYZING)
            findings.extend(analyze_nikto(raw_outputs.get("nikto", ""), target, url=url))

            if inv_mode is InvestigationMode.DEEP:
                await self._gate()
                self._state(AgentState.EXECUTING)
                iterations += 1
                entry = await self._execute("nuclei_cve", url, raw_outputs, notes, timeout_s=900.0)
                if entry is not None and entry[0] == 127:
                    notes.append("nuclei is not installed in the sandbox image; template scan skipped.")
                    self._emit(EventType.OBSERVATION, message="nuclei not available in sandbox; skipped")
                else:
                    self._state(AgentState.ANALYZING)
                    findings.extend(analyze_nuclei(raw_outputs.get("nuclei_cve", ""), target))
        else:
            notes.append("No web service observed on common HTTP ports; web checks were not run.")

        if inv_mode is InvestigationMode.DEEP and domain_mode:
            await self._gate()
            iterations += 1
            await self._execute("whois", target, raw_outputs, notes)

        findings = dedupe_findings(findings)[:_MAX_FINDINGS]
        for finding in findings:
            self._emit(
                EventType.FINDING,
                title=finding.title,
                severity=finding.severity,
                asset=finding.asset,
                summary=finding.summary,
                confidence=finding.confidence,
            )

        self._state(AgentState.REPLANNING)
        self._state(AgentState.REPORT_GENERATION)
        if self._check_stopped is not None and self._check_stopped():
            stop_reason = "stopped"
        self._state(AgentState.COMPLETED)
        duration = time.monotonic() - started
        self._emit(
            EventType.OBSERVATION,
            message=f"Local recon completed: {iterations} real tool executions, {len(findings)} findings, {duration:.1f}s",
        )
        return ReconResult(
            findings=findings,
            stop_reason=stop_reason,
            iterations=iterations,
            events=list(self._events),
            raw_outputs=raw_outputs,
            duration_s=duration,
            notes=notes,
        )

    async def _execute(
        self,
        tool_name: str,
        target_value: str,
        raw_outputs: dict[str, str],
        notes: list[str],
        *,
        timeout_s: float = 300.0,
    ) -> tuple[int, str] | None:
        """Run one real tool; returns (exit_code, stdout) or None on policy/launch error."""
        try:
            argv = self._tool_argv(tool_name, target_value, InvestigationMode.DEEP)
        except Exception as exc:
            notes.append(f"{tool_name}: not schedulable ({exc})")
            self._emit(EventType.POLICY_BLOCK, tool=tool_name, error=str(exc))
            return None
        self._emit(EventType.TOOL_STARTED, tool=tool_name, argv=argv)
        try:
            outcome: ToolOutcome = await self._executor.run(tool_name, argv, timeout_s=timeout_s)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            notes.append(f"{tool_name}: execution failed ({exc})")
            self._emit(EventType.ERROR, tool=tool_name, error=str(exc))
            return None
        combined = (outcome.stdout or "") + (("\n" + outcome.stderr) if outcome.stderr else "")
        if len(combined) > _MAX_OUTPUT:
            combined = combined[:_MAX_OUTPUT] + "\n[output truncated]"
        raw_outputs[tool_name] = combined
        self._emit(
            EventType.TOOL_COMPLETED,
            tool=tool_name,
            exit_code=outcome.exit_code,
            timed_out=outcome.timed_out,
            bytes=len(combined),
        )
        if self._on_tool_output is not None:
            self._on_tool_output(tool_name, argv, combined)
        if outcome.exit_code != 0:
            notes.append(f"{tool_name}: exited {outcome.exit_code}")
        return outcome.exit_code, combined
