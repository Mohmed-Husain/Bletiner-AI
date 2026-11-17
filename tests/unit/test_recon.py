"""Unit tests for the deterministic local recon engine (no Docker, no network)."""

from __future__ import annotations

import asyncio

import pytest

from bletiner.agent.events import AgentState, EventType
from bletiner.agent.loop import ToolOutcome
from bletiner.services.recon import (
    DetectedFinding,
    ReconEngine,
    analyze_http_headers,
    analyze_nikto,
    analyze_nmap,
    analyze_nuclei,
    analyze_whatweb,
    dedupe_findings,
    parse_http_headers,
    parse_nmap_ports,
)
from bletiner.tools.catalog import default_registry

NMAP_FAST = """
Starting Nmap 7.99 ( https://nmap.org )
Nmap scan report for example.com (93.184.216.34)
Host is up (0.024s latency).
Not shown: 97 closed tcp ports (conn-refused)
PORT     STATE SERVICE
22/tcp   open  ssh
80/tcp   open  http
443/tcp  open  https
3306/tcp open  mysql
8080/tcp closed http-proxy
Nmap done: 1 IP address (1 host up) scanned in 1.48 seconds
"""

NMAP_SERVICE = """
PORT     STATE SERVICE  VERSION
22/tcp   open  ssh      OpenSSH 8.2p1 Ubuntu 4ubuntu0.5 (Ubuntu Linux; protocol 2.0)
80/tcp   open  http     Apache httpd 2.4.41 ((Ubuntu))
443/tcp  open  https    nginx 1.18.0 (Ubuntu)
3306/tcp open  mysql    MySQL 5.7.33
"""

HTTP_HEAD = """HTTP/1.1 200 OK
Date: Mon, 16 Nov 2026 12:05:00 GMT
Server: Apache/2.4.41 (Ubuntu)
X-Powered-By: PHP/7.4.3
Content-Type: text/html; charset=UTF-8
Connection: close
"""

WHATWEB = "http://example.com [200 OK] Apache[2.4.41], PHP[7.4.3], jQuery[1.12.4], HTTPServer[Ubuntu Linux]"

NIKTO = """
- Nikto v2.5.0
+ Target IP:          93.184.216.34
+ Target Hostname:    example.com
+ Target Port:        80
+ Server: Apache/2.4.41 (Ubuntu)
+ The anti-clickjacking X-Frame-Options header is not present.
+ OSVDB-3092: /: Apache/2.4.41 appears to be outdated.
+ /admin/: Admin login page/section found.
+ 7916 requests: 0 error(s) and 2 item(s) reported on remote host
+ 1 host(s) tested
"""

NUCLEI = "[CVE-2021-41773] [http] [critical] http://example.com/cgi-bin/.%2e/.%2e/etc/passwd\n"


class FakeExecutor:
    def __init__(self, outputs: dict[str, str]) -> None:
        self.outputs = outputs
        self.calls: list[tuple[str, list[str]]] = []

    async def run(self, tool_name: str, argv: list[str], timeout_s: float) -> ToolOutcome:
        self.calls.append((tool_name, argv))
        return ToolOutcome(exit_code=0, stdout=self.outputs.get(tool_name, "ok\n"), stderr="")


def test_parse_nmap_ports_only_open() -> None:
    ports = parse_nmap_ports(NMAP_FAST)
    assert [p.port for p in ports] == [22, 80, 443, 3306]
    assert ports[0].service == "ssh"
    assert all("closed" not in p.state for p in ports)


def test_analyze_nmap_flags_exposure_and_versions() -> None:
    findings = analyze_nmap(NMAP_SERVICE, "example.com")
    titles = {f.title for f in findings}
    assert "MySQL service exposed" in titles
    assert "Outdated Apache HTTP Server" in titles
    assert "Outdated OpenSSH Server" in titles
    assert "Outdated nginx Server" in titles
    assert "Outdated PHP Runtime" not in titles
    mysql = next(f for f in findings if f.title == "MySQL service exposed")
    assert mysql.asset == "example.com:3306"
    assert mysql.severity == "MEDIUM"


def test_analyze_nmap_no_findings_for_plain_ssh() -> None:
    output = "22/tcp open  ssh     OpenSSH 9.6p1 Ubuntu 3ubuntu13\n"
    titles = {f.title for f in analyze_nmap(output, "example.com")}
    assert "Outdated OpenSSH Server" not in titles
    assert "SSH service exposed" not in titles


def test_parse_http_headers_is_case_insensitive() -> None:
    headers = parse_http_headers(HTTP_HEAD)
    assert headers["server"] == "Apache/2.4.41 (Ubuntu)"
    assert headers["x-powered-by"] == "PHP/7.4.3"


def test_analyze_http_headers_reports_missing_and_disclosure() -> None:
    findings = analyze_http_headers(HTTP_HEAD, "example.com", url="https://example.com")
    titles = {f.title for f in findings}
    assert "Missing HTTP security headers" in titles
    assert "Missing HSTS header on HTTPS" in titles
    assert "Web server version disclosed" in titles
    assert "X-Powered-By header disclosed" in titles


def test_analyze_http_headers_clean_response_has_no_missing_headers() -> None:
    clean = (
        "HTTP/1.1 200 OK\nServer: nginx\nX-Content-Type-Options: nosniff\n"
        "X-Frame-Options: DENY\nContent-Security-Policy: default-src 'self'\n"
        "Strict-Transport-Security: max-age=31536000\n"
    )
    findings = analyze_http_headers(clean, "example.com", url="https://example.com")
    titles = {f.title for f in findings}
    assert "Missing HTTP security headers" not in titles
    assert "Missing HSTS header on HTTPS" not in titles


def test_analyze_whatweb_flags_old_stack() -> None:
    findings = analyze_whatweb(WHATWEB, "example.com", url="http://example.com")
    titles = {f.title for f in findings}
    assert "Outdated Apache HTTP Server detected" in titles
    assert "Outdated PHP runtime detected" in titles
    assert "Outdated jQuery library detected" in titles


def test_analyze_whatweb_current_stack_is_quiet() -> None:
    current = "http://example.com [200 OK] Apache[2.4.62], PHP[8.3.1], jQuery[3.7.1]"
    assert analyze_whatweb(current, "example.com", url="http://example.com") == []


def test_analyze_nikto_classifies_and_ignores_metadata() -> None:
    findings = analyze_nikto(NIKTO, "example.com", url="http://example.com")
    assert 1 <= len(findings) <= 8
    texts = " ".join(f.title for f in findings)
    assert "anti-clickjacking" in texts
    assert "outdated" in texts
    assert "Target IP" not in texts
    assert all(f.verification == "NEEDS_TRIAGE" for f in findings)


def test_analyze_nuclei_parses_severity() -> None:
    findings = analyze_nuclei(NUCLEI, "example.com")
    assert len(findings) == 1
    assert findings[0].severity == "CRITICAL"
    assert "CVE-2021-41773" in findings[0].title


def test_dedupe_keeps_strongest_severity() -> None:
    low = DetectedFinding("Same title", "LOW", "MEDIUM", "example.com:80", "s")
    high = DetectedFinding("same title", "HIGH", "HIGH", "EXAMPLE.com:80", "s")
    result = dedupe_findings([low, high])
    assert len(result) == 1
    assert result[0].severity == "HIGH"


@pytest.mark.asyncio
async def test_engine_runs_real_playbook_with_fake_executor() -> None:
    executor = FakeExecutor(
        {
            "dig": ";; ANSWER SECTION:\nexample.com. 300 IN A 93.184.216.34\n",
            "nmap_fast": NMAP_FAST,
            "nmap_service": NMAP_SERVICE,
            "http_head": HTTP_HEAD,
            "whatweb": WHATWEB,
            "nikto": NIKTO,
        }
    )
    events = []
    outputs = []
    engine = ReconEngine(
        executor,
        default_registry(),
        on_event=events.append,
        on_tool_output=lambda tool, argv, out: outputs.append((tool, argv, out)),
    )
    result = await engine.run("example.com", mode="deep")
    assert result.stop_reason == "completed"
    assert result.iterations >= 7
    assert result.findings
    assert "Starting Nmap" in result.raw_outputs["nmap_fast"]
    called = {tool for tool, _ in executor.calls}
    assert {"dig", "whois", "nmap_fast", "nmap_service", "http_head", "whatweb", "nikto"} <= called
    states = [e.payload.get("state") for e in events if e.type is EventType.STATE_CHANGED]
    assert AgentState.INITIALIZING.value in states
    assert AgentState.COMPLETED.value in states
    assert any(e.type is EventType.FINDING for e in events)
    assert any(tool == "nmap_fast" for tool, _argv, _out in outputs)
    # Every emitted argv is a real structured argv (no shell wrapper).
    fast_argv = next(argv for tool, argv in executor.calls if tool == "nmap_fast")
    assert fast_argv[0] == "nmap" and "example.com" in fast_argv


@pytest.mark.asyncio
async def test_engine_stops_when_operator_stops() -> None:
    engine = ReconEngine(
        FakeExecutor({}),
        default_registry(),
        check_stopped=lambda: True,
    )
    with pytest.raises(asyncio.CancelledError):
        await engine.run("example.com", mode="basic")
