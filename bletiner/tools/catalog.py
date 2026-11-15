"""Default static tool catalog (DOC-04 section 6.2). Commands are argv templates; the launcher prefix and
ambient-cap handling are applied by ``bletiner.container.exec_runner``, not here.
"""

from __future__ import annotations

from bletiner.tools.registry import ToolDef, ToolRegistry, ToolTier

_TARGET = {"target": {"type": "string", "description": "authorized host or IP"}}


def default_tools() -> list[ToolDef]:
    return [
        # Tier 1 - passive
        ToolDef("whois", ToolTier.PASSIVE, "WHOIS registration lookup", ("whois", "{target}"), _TARGET),
        ToolDef(
            "dig", ToolTier.PASSIVE, "DNS record lookup", ("dig", "+noall", "+answer", "{target}"), _TARGET
        ),
        ToolDef(
            "whatweb",
            ToolTier.PASSIVE,
            "Passive web fingerprint",
            ("whatweb", "--no-errors", "{target}"),
            _TARGET,
        ),
        ToolDef(
            "http_head",
            ToolTier.PASSIVE,
            "Single HEAD request",
            ("curl", "-sS", "-I", "--max-redirs", "0", "{target}"),
            _TARGET,
        ),
        # Tier 2 - active recon (nmap connect scan only; raw SYN unavailable per R-09)
        ToolDef(
            "nmap_service",
            ToolTier.ACTIVE_RECON,
            "TCP connect service/version scan",
            ("nmap", "-sT", "-sV", "-T3", "--max-retries", "1", "{target}"),
            _TARGET,
            max_rate_pps=50,
        ),
        ToolDef(
            "nmap_fast",
            ToolTier.ACTIVE_RECON,
            "Fast top-100 connect scan",
            ("nmap", "-sT", "-F", "-T3", "{target}"),
            _TARGET,
            max_rate_pps=50,
        ),
        ToolDef(
            "nikto",
            ToolTier.ACTIVE_RECON,
            "Safe web server audit",
            ("nikto", "-Tuning", "1,2,3,b", "-host", "{target}"),
            _TARGET,
            max_rate_pps=50,
        ),
        # Tier 3 - intrusive (DEEP only)
        ToolDef(
            "nuclei_cve",
            ToolTier.INTRUSIVE,
            "Non-destructive CVE/misconfig templates",
            (
                "nuclei",
                "-tags",
                "cve,misconfig",
                "-severity",
                "low,medium,high",
                "-rl",
                "20",
                "-target",
                "{target}",
            ),
            _TARGET,
            max_rate_pps=20,
        ),
        ToolDef(
            "ffuf",
            ToolTier.INTRUSIVE,
            "Rate-limited content discovery",
            ("ffuf", "-rate", "20", "-u", "{target}"),
            _TARGET,
            max_rate_pps=20,
        ),
        # Tier 4 - destructive (registered so the gate can recognise and reject them)
        ToolDef(
            "masscan",
            ToolTier.DESTRUCTIVE,
            "High-rate port scanner (BLOCKED)",
            ("masscan", "{target}"),
            _TARGET,
        ),
        ToolDef(
            "sqlmap_exploit",
            ToolTier.DESTRUCTIVE,
            "SQL injection exploitation (BLOCKED)",
            ("sqlmap", "--batch", "--os-shell", "-u", "{target}"),
            _TARGET,
        ),
        ToolDef(
            "hydra", ToolTier.DESTRUCTIVE, "Credential brute force (BLOCKED)", ("hydra", "{target}"), _TARGET
        ),
        ToolDef(
            "metasploit",
            ToolTier.DESTRUCTIVE,
            "Exploitation framework (BLOCKED)",
            ("msfconsole", "{target}"),
            _TARGET,
        ),
    ]


def default_registry() -> ToolRegistry:
    return ToolRegistry(default_tools())
