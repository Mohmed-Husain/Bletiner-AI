# Bletiner AI — Open Decisions and Assumptions Register

**Document ID:** `DOC-13`  
**Version:** 3.1.0 (Revision 3.1 — Technical Correction & Empirical Validation Baseline)  
**Status:** Approved Master Architecture Register  
**Authors:** Lead Systems Architect & Claude Code Technical Author  
**Target Audience:** System Architects, Core Developers, Security Reviewers, Claude Code Implementers  

---

## 1. Document Purpose & Scope

This document serves as the authoritative register of:
1. **Confirmed Product Decisions** derived directly from the Bletiner AI Master Product Specification (Sections 1 & 2), the Pi-harness directives, and the **Revision 3.1 Technical Correction Audit**, which must be respected as immutable engineering requirements.
2. **Revision 3.1 Deep Technical Resolutions:**
   * **Host-Side Netns Route Injection:** Setting the default gateway on the zero-CAP_NET_ADMIN Kali container using an ephemeral setup container attached to Kali's network namespace (`--network=container:...`).
   * **Strict Separation of Domain SNI Proxying vs Dedicated IP Forwarding:** Explicit prohibition of raw IP forwarding for multi-tenant CDN targets; domain targets route strictly through the SNI/Host proxy.
   * **Ambient Capabilities Verification:** Non-root user `bletiner` with ambient capabilities under `no_new_privs: true`, verifiable via `/proc/$$/status` (`CapAmb`).
   * **DNS HTTPS/SVCB Record Stripping (Type 65):** Mitigating TLS Encrypted Client Hello (ECH) by stripping HTTPS DNS records at the gateway resolver to force fallback to standard inspectable SNI.
   * **Empirical Verification Standards:** Clear separation between Documentation Consistency, Implementation Correctness, and Empirically Verified Properties (marked PENDING LAB EXECUTION until live test runs).
3. **Engineering Recommendations** provided with technical justification, alternatives analyzed, and trade-offs.
4. **Assumptions** made during architectural specification, labeled conservatively.
5. **Open Decisions & Questions** requiring explicit stakeholder or user confirmation.
6. **Technical Risks & Mitigations** covering prompt injection, scope escape, raw socket leaks, and ECH encryption bypass.
7. **Trade-Off Analysis Matrix** evaluating primary design decisions across performance, security, complexity, and maintainability.

---

## 2. Confirmed Product Decisions (Including Revision 3.1 Resolutions)

The following architectural and design decisions are confirmed product requirements. Claude Code and any implementing engineers must treat these as non-negotiable architectural constraints.

### 2.1 Evolution & Technology Stack Decisions
* **Historical Evolution:** Evolved from the single-file prototype `lm_terminal_agent.py` (which paired a standard OpenAI client with a local terminal) into **Bletiner AI**, a production-grade, asynchronous Python desktop application built for autonomous security investigation.
* **Primary Language:** Python 3.11+ (leveraging modern typing, `asyncio` task groups, pattern matching, strict Protocol classes, and performance enhancements).
* **Desktop UI Framework:** PySide6 (Qt for Python). The desktop GUI launches automatically when the user executes the `bletiner` entry-point command from the terminal.
* **Agent Harness:** Independent implementation of a Pi-inspired autonomous agent loop (state machine, event streaming, tool lifecycle, dynamic steering, context truncation, and durable checkpoints). No proprietary code or unlicensed components from external projects are copied.
* **Container Orchestration:** Docker via the official `docker-py` Python SDK, controlling per-investigation Linux containers.
* **Security Execution Environment:** Dedicated Kali Linux container per investigation session.
* **Persistent Storage:** SQLite database with Write-Ahead Logging (WAL) enabled, complemented by structured filesystem directories per investigation.
* **Deliverable Reports:** Dual-format output consisting of clean, standard Markdown (`report.md`) and a self-contained, air-gapped HTML report (`report.html`) with embedded styles and no external CDN dependencies.

### 2.2 Core Investigation Workflow & Agent Pattern
* **Investigation Workflow:** Standardized autonomous pipeline:  
  $$\text{Plan} \longrightarrow \text{Execute} \longrightarrow \text{Observe} \longrightarrow \text{Analyze} \longrightarrow \text{Re-plan}$$
* **Dynamic Planning:** The agent must not execute a hardcoded linear script of tools. Tool invocation is dynamically computed based on observed evidence, confidence levels, scope boundaries, policy, and budget constraints.
* **Investigation Depth:**
  * **Basic Investigation:** Rapid attack-surface mapping, service discovery, standard vulnerability checks, evidence collection, and preliminary reporting.
  * **Deep Investigation:** Includes all Basic phase tasks, plus multi-stage verification, correlation across services, attack-surface depth expansion within scope, active false-positive reduction, and in-depth evidence collection.
  * **Continuation Support:** Users can seamlessly transition a completed Basic investigation into a Deep investigation. Prior observations and evidence are reused safely without re-running redundant discovery scans.

### 2.3 Autonomy, Human Controls & Steering
* **Default Mode:** Full autonomous execution within authorized scope.
* **Human-in-the-Loop Controls:**
  * **Pause:** Halts scheduling of new agent tasks, checkpoints current state, and allows running sub-processes to reach a safe checkpoint or cancellation point.
  * **Resume:** Re-validates container health, restores durable checkpoints, checks idempotency of incomplete tasks, and resumes autonomous scheduling.
  * **Stop:** Deliberately terminates the investigation run, captures current partial findings, generates reports on accumulated evidence, and transitions status to `STOPPED`.
  * **Steer (Instruction Injection):** Allows the operator to inject real-time natural language steering instructions into the agent’s task queue without wiping prior context, valid evidence, or existing findings.
* **Silent Autonomous Operation:** The application is an investigative workspace, not a conversational chatbot. The agent works quietly without conversational filler, emitting concise, structured operational events (`InvestigationInitialized`, `ContainerCreated`, `ToolStarted`, `ObservationParsed`, `FindingConfirmed`, etc.). Raw internal chain-of-thought is never exposed to the UI.

### 2.4 Container Sandbox & Non-Root Ambient Capabilities (Audit Resolution #2 & Rev 3.1)
* **Non-Root Execution as `bletiner` (UID 1000):**
  * Kali container runs under the dedicated unprivileged user `bletiner`.
  * Security flags: `privileged=False`, `cap_drop=['ALL']`, `read_only=True`, `tmpfs={'/tmp': 'size=512m'}`, `no_new_privs: true`, zero Docker socket mounts.
* **Ambient Capabilities for Raw Sockets under `no_new_privs`:**
  * When `no_new_privs: true` is enforced, the Linux kernel ignores filesystem capabilities (`setcap`).
  * Process launcher invokes `setpriv --reuid=1000 --regid=1000 --init-groups --inh-caps=+net_raw,+net_bind_service --ambient-caps=+net_raw,+net_bind_service` (or `capsh`).
  * **Verification Standard:** Inspected via `/proc/$$/status`: `CapAmb` must have bit 13 (`CAP_NET_RAW`) and bit 10 (`CAP_NET_BIND_SERVICE`) set, while bit 12 (`CAP_NET_ADMIN`) must be strictly `0`.
* **Zero `CAP_NET_ADMIN` in Kali:**
  * **Kali has ZERO `CAP_NET_ADMIN` and is NEVER trusted to firewall itself.** No `iptables` or `nftables` commands run inside Kali.

### 2.5 Dual-Homed Egress Gateway & Host-Side Netns Route Injection (ADR-016 / Rev 3.1)
* **Dual-Homed Fail-Closed Topology:**
  * `bletiner-internal-<session_id>`: Created with `internal: true` (no default route to host or internet). The Kali container connects ONLY to this network.
  * `bletiner-gateway-<session_id>`: Dedicated lightweight proxy/gateway container connected to BOTH `bletiner-internal-<session_id>` AND the external Docker bridge (`bletiner-external`).
  * **Strict Fail-Closed Guarantee:** If the gateway container terminates, the internal network has zero outbound connectivity. All traffic is physically contained.
* **Host-Side Netns Default Route Injection (ADR-016):**
  * *The Routing Dilemma:* Docker bridge networks created with `internal: true` intentionally provision containers with NO default gateway. Because Kali has zero `CAP_NET_ADMIN`, it cannot configure its own default route via `ip route add default via <gateway_ip>`.
  * *The Host-Side Resolution:* During container bootstrap, the host orchestrator executes a lightweight ephemeral setup micro-container attached directly to Kali's network namespace:
    ```bash
    docker run --rm --network=container:bletiner-kali-<session_id> \
      --cap-add=NET_ADMIN alpine ip route replace default via <gateway_internal_ip>
    ```
  * Once the default route is injected, the setup container immediately exits. Kali's default route now transparently routes all non-local traffic to `bletiner-gateway`, while Kali remains permanently unprivileged with zero `CAP_NET_ADMIN`.

### 2.6 Strict Separation: Domain SNI Proxying vs Dedicated IP Forwarding (ADR-018 / Rev 3.1)
* *The Multi-Tenant CDN Vulnerability:* If target is `example.com` hosted on Cloudflare (`104.16.132.229`), adding that IP to an iptables raw FORWARD allowlist would allow Kali to send arbitrary raw packets to millions of other unauthorized customer domains co-hosted on that same Cloudflare IP.
* *The Enforcement Boundary (ADR-018):*
  * **Domain / URL Targets:** ZERO iptables packet forwarding rules are added. All traffic to domain targets must flow exclusively through the gateway HTTP/HTTPS SNI proxy (`http://bletiner-gateway:8080`). Direct raw socket connections to CDN IPs are dropped.
  * **Dedicated IP / CIDR Targets:** Packet forwarding via `iptables` FORWARD is enabled ONLY when the target is explicitly specified as an authorized dedicated IP address or CIDR range.

### 2.7 DNS HTTPS/SVCB Record Stripping to Counter TLS ECH (ADR-017 / Rev 3.1)
* *The Threat:* TLS Encrypted Client Hello (ECH) encrypts the SNI inside the TLS Client Hello using public keys published in DNS type 65 `HTTPS` or `SVCB` resource records. If ECH succeeds, the gateway SNI proxy cannot inspect the domain name.
* *The Mitigation (ADR-017):* The gateway's internal DNS resolver intercepts all DNS traffic and **strips type 65 HTTPS / SVCB records**. When client browsers or security tools receive no HTTPS/SVCB records, they safely fall back to standard unencrypted TLS 1.3 SNI, allowing full transparent gateway domain inspection.

### 2.8 Two-Tier Redirect Validation (CONNECT Encrypted Header Blindness Resolution)
* Standard forward HTTP proxies cannot inspect HTTP 301/302 `Location` headers inside end-to-end encrypted HTTPS `CONNECT` tunnels without intrusive MITM certificate spoofing.
* **Layer A (Client-Side Tool Hook):** High-level Python tool wrappers and HTTP clients configure `allow_redirects=False`. When an HTTP 301/302 response is received, the client inspects the `Location` header, parses the target domain/IP, validates it against `ScopeSpec`, and aborts the request if out of scope.
* **Layer B (Gateway SNI Drop):** If a rogue tool attempts to initiate a new TCP connection or CONNECT tunnel to an out-of-scope domain, the egress gateway intercepts the TLS Client Hello, inspects the **Server Name Indication (SNI)**, and immediately drops the connection before any application payload is transmitted.

### 2.9 4-Tier Tool Risk Classification & Autonomous Scheduling Boundary
All executable tools are partitioned into four strict operational risk tiers:
* **Tier 1 (Passive Reconnaissance):** Zero target packet generation beyond public DNS/whois (`whois`, `dnsrecon`, `whatweb`). Always permitted in Basic and Deep modes.
* **Tier 2 (Safe Active Reconnaissance):** Standard non-intrusive TCP handshakes, banner grabbing, and port discovery (`nmap` service discovery, `wafw00f`). Permitted within authorized scope subject to global rate limits (default max 50 packets/sec).
* **Tier 3 (Bounded Vulnerability Verification):** Controlled vulnerability template matching (`nuclei` safe templates, `nikto` rate-limited). Permitted only on discovered active services.
* **Tier 4 (Intrusive / Destructive Tools):** Mass high-rate port scanners (`masscan`), automated SQL injection exploiters (`sqlmap` exploit modes), network brute-forcers (`hydra`), and exploit frameworks.
  * **INVIOLABLE RULE:** **Tier 4 tools are strictly BLOCKED from autonomous agent scheduling.** The agent loop will reject any plan proposing Tier 4 tools with a `PolicyViolationError`. Tier 4 tools may only be invoked manually by the human operator inside the interactive Kali terminal.

### 2.10 10-Point Egress Validation PoC Gate & Empirical Test Distinctions
* **Empirical Verification Principle:** Every security test must report: Test ID, Exact command, Expected result, Actual result, Verdict (PASS / FAIL / NOT RUN), and Relevant raw logs.
* **Three Levels of Assurance:**
  1. *Documentation Consistency:* Architecture aligns with product requirements (Verified).
  2. *Implementation Correctness:* Source code adheres to protocols and passes static typing (Verified in Phase 0-2).
  3. *Empirically Verified Security Properties:* Properties requiring live network/container validation (e.g. fail-closed egress) are marked **PENDING LAB EXECUTION** until `scripts/verify_gateway_poc.py` runs against live Docker.
* **Mandatory Phase 3 Exit Gate:** Before Claude Code begins Phase 4 (Agent Runtime), Claude Code must construct and pass `scripts/verify_gateway_poc.py` on the target workstation (Docker Desktop WSL2 / Linux) across all 10 adversarial failure scenarios.

### 2.11 Cross-Platform Validation Reality: Windows Docker Desktop (WSL2) vs Native Linux
* On Windows, Docker Desktop operates inside a lightweight WSL2 Linux VM. Host Windows iptables do not exist.
* The dual-homed Docker bridge + containerized gateway architecture works identically and 100% portably across Windows (Docker Desktop WSL2), Linux, and macOS without requiring host administrator privileges.

### 2.12 Tool Execution via Structured `argv: list[str]` (Command Injection Immunity)
* **Strict Prohibition of Raw Shell Concatenation:** Tools must NEVER be executed via raw string concatenation (e.g., `bash -c "nmap " + target`).
* **Structured Arguments Only:** All commands executed inside Kali via Docker Exec must be dispatched as structured argument arrays (`argv: list[str]`), invoking Linux `execve` directly without invoking a subshell.

### 2.13 Model Context Protocol (MCP) Host Boundary (Audit Resolution #5)
* **Host-Level MCP Deferred:** Executing third-party Model Context Protocol (MCP) server binaries directly on the host workstation is deferred from v1.
* **Containerized Tooling Only:** Any external tools or plugins execute strictly inside the Kali container or as isolated Docker microservice sidecars.

### 2.14 LLM Provider Abstraction & Model Locking (Pi-Harness Adaptation)
* **Unified Provider Interface (`pi-ai`):** Pluggable adapter architecture normalizing OpenAI, Anthropic, Google Gemini, DeepSeek, Ollama, LM Studio, OpenRouter, Groq, Together AI, Mistral, and custom OpenAI-compatible endpoints.
* **Domain Model Independence:** Domain code interacts solely with unified `AgentMessage` objects; translation to provider-specific payloads occurs exclusively at the LLM adapter boundary.
* **Model Locking:** Once an investigation begins, the model and provider parameters are immutable for the duration of that session to guarantee reproducibility.

### 2.15 Findings Architecture & Non-Destructive Policy
* **Finding Lifecycle:** Strictly typed state progression:
  $$\text{Detected} \longrightarrow \text{Needs Triage} \longrightarrow \text{Under Investigation} \longrightarrow \{\text{Verified} \mid \text{False Positive} \mid \text{Inconclusive}\} \longrightarrow \text{Reported}$$
* **Decoupled Severity & Confidence:** Severity (impact) and Confidence (evidence certainty) are evaluated as independent metrics. Low-priority or unverified findings remain tracked in the session database.
* **Non-Destructive Guarantee:** By default, all automated tools and checks are strictly non-destructive.

---

## 3. Engineering Recommendations (Refined for Revision 3.1)

To operationalize the confirmed requirements safely and effectively, the architecture team has evaluated alternatives and established the following formal technical recommendations.

### 3.1 PySide6 Concurrency: `qasync` Event Loop Integration
* **Recommendation:** Integrate `qasync` to run `asyncio` natively inside the Qt event loop on the main GUI thread. Eliminates multi-threaded locking for UI state updates; allows native `async`/`await` in controllers; heavy blocking computations are dispatched via `asyncio.to_thread()`.

### 3.2 Shared Terminal Implementation: Qt WebEngine + xterm.js Local WebSocket Bridge
* **Recommendation:** Embed `QWebEngineView` rendering a localized, air-gapped `xterm.js` terminal, connected via a loopback WebSocket bridge (`127.0.0.1:<random-port>`) directly to a Docker PTY Exec instance with cryptographically random token authentication (`X-Bletiner-Auth`).

### 3.3 Network Egress Enforcement: Dual-Homed Bridge + Gateway with Netns Route Injection
* **Recommendation:** Deploy a dual-homed bridge network (`bletiner-internal` with `internal: true` + `bletiner-gateway`).
  * Injects default route via host-side ephemeral netns setup container (`docker run --network=container:...`).
  * Gateway inspects TLS SNI and HTTP Host headers, resolves DNS independently, strips type 65 HTTPS records, and drops raw IP packets to CDN targets.
  * If the gateway container terminates, the internal network retains zero external connectivity (fail-closed).

### 3.4 Kali Container Image Strategy: Pre-Baked, Version-Pinned Dockerfile
* **Recommendation:** Provide a pre-built, version-pinned Docker image (`bletiner/kali-investigator:1.0.0`) based on `kalilinux/kali-rolling` with pre-installed security tools (`nmap`, `masscan`, `nikto`, `nuclei`, `dnsrecon`, `whois`, `curl`, `ffuf`, `whatweb`, `wafw00f`).

### 3.5 Persistence Layer: SQLite with WAL Mode & Pydantic Serialization
* **Recommendation:** SQLite configured with `PRAGMA journal_mode = WAL;`, `PRAGMA synchronous = NORMAL;`, and `PRAGMA foreign_keys = ON;`. Application service layer utilizes Pydantic v2 for data validation and schema-to-dict serialization.

### 3.6 Secrets Storage: OS Keyring Service with Encrypted Fallback
* **Recommendation:** Utilize Python `keyring` library to interface with platform-native credential managers (Windows Credential Manager, Apple Keychain, Linux Secret Service API) with AES-256-GCM encrypted local master store fallback.

### 3.7 Safe HTML Report Generation: Jinja2 Air-Gapped Templating
* **Recommendation:** Jinja2 template engine with strict autoescaping enabled (`autoescape=True`), inlined styles/SVGs, air-gapped assets (zero external HTTP requests), and embedded Content Security Policy (`default-src 'none'; style-src 'unsafe-inline'; img-src data:;`).

---

## 4. Assumptions Register

| ID | Category | Assumption Statement | Rationale | Impact if Invalidated |
|---|---|---|---|---|
| **ASM-001** | Host Environment | Host workstation has Docker Engine / Docker Desktop (v24.0+) installed and running with permissions for the current user. | Docker is the mandatory runtime for Kali containers. Bletiner AI does not package a bundled Docker daemon. | Application cannot start investigations. Must display pre-flight error screen guiding Docker installation. |
| **ASM-002** | Host OS Support | Primary operating systems are Windows 10/11 (with WSL2 backend), macOS 13+ (Apple Silicon & Intel), and modern Linux (Ubuntu 22.04+, Fedora 38+). | PySide6 and Docker Desktop have first-class support on these platforms. | Non-standard operating systems may experience PTY terminal bridge or GUI scaling anomalies. |
| **ASM-003** | Workstation Model | Bletiner AI operates as a single-user desktop application running locally under the host user's security context. | Designed for security consultants, internal penetration testers, and security researchers on their personal workstations. | If deployed as a multi-user shared server, additional authentication, session segregation, and host sandboxing are required. |
| **ASM-004** | LLM Connectivity | The host application has direct outbound internet access (or corporate proxy access) to reach cloud LLM endpoints (Anthropic, OpenAI, Gemini). | The desktop app orchestrates the agent and passes prompts directly to providers. The Kali container does *not* talk to the LLM. | If host is completely offline, only local models (Ollama, LM Studio) can be utilized. |
| **ASM-005** | Tool Execution Scope | Tools executed inside the Kali container communicate *only* with the authorized target, never with the host loopback or Docker internal networks. | Enforced by dual-homed egress proxy, default routing, and network namespace controls. | Potential scope escape or unauthorized internal reconnaissance if network isolation fails. |
| **ASM-006** | Local Models Location | Local LLMs (Ollama / LM Studio) run as host processes or on a reachable LAN gateway, exposed via standard HTTP APIs (e.g., `http://localhost:11434`). | Containerized models inside Kali would cause severe resource contention and memory exhaustion. | Desktop app connects to host localhost for Ollama; container network isolation does not affect this connection. |
| **ASM-007** | Target Responsiveness | Target systems may exhibit rate limiting, transient packet loss, or WAF blocks during scanning. | Real-world security assessments frequently encounter network jitter and filtering. | Agent harness must implement per-tool execution timeouts (default 300s) and exponential retry policies. |
| **ASM-008** | Non-Destructive Intent | The user uses Bletiner AI solely for authorized security assessments, posture validation, and discovery. | Reflected in legal terms, default tool configurations, and prompt policy. | Malicious usage violates TOS; architecture enforces safe tool arguments to prevent accidental denial of service. |
| **ASM-009** | Display Environment | The user workstation has a minimum screen resolution of 1280x800 for the 3-card GUI layout. | Complex investigative workspaces require sufficient screen real estate for logs, terminal, and session panels. | Panels collapse into collapsible drawers/tabs on screens narrower than 1200px. |
| **ASM-010** | Execution Budgets | Tool executions default to 300-second timeouts unless overridden by specific tool manifests. | Prevents hung network connections (e.g. stalled TCP handshakes) from blocking the autonomous agent loop indefinitely. | Hung tools are cancelled with SIGTERM, escalated to SIGKILL after a 10-second grace window. |

---

## 5. Open Questions & Decisions Requiring User Approval

### 5.1 Decision Req-01: Scope Expansion Confirmation Policy
* **Description:** When an agent discovers an in-scope web application that redirects to an unlisted subdomain, how should the application handle scope expansion?
* **Current Default:** Strict Modal Confirmation (pause autonomous workflow and display high-priority UI prompt).

### 5.2 Decision Req-02: Maximum Concurrent Active Investigations
* **Description:** How many simultaneous active Docker containers and LLM agent loops should execute concurrently?
* **Current Default:** Limit of 2 Concurrent Investigations (configurable in settings up to ceiling of 5).

### 5.3 Decision Req-03: Windows/WSL2 Docker Egress Enforcement Architecture
* **Status:** **CONFIRMED & RESOLVED in Revision 2 & 3.1**.
* **Resolution:** Implemented via Dual-Homed Bridge + Gateway Container Sidecar (`bletiner-internal` with `internal: true` + `bletiner-gateway` with host-side netns route injection). 100% portable across Windows WSL2, macOS, and Linux.

### 5.4 Decision Req-04: Basic-to-Deep Observation Stale TTL
* **Description:** What is the maximum time-to-live (TTL) after which network observations are considered stale?
* **Current Default:** 24-Hour Observation TTL (observations older than 24h trigger lightweight delta-scan before Deep verification).

### 5.5 Decision Req-05: Fallback Terminal Strategy for Minimal Linux Hosts
* **Description:** Terminal fallback if `QtWebEngine` dependencies are missing on minimal Linux.
* **Current Default:** Pure PySide6 ANSI Canvas Fallback using `pyte`.

### 5.6 Decision Req-06: Target PII & Internal Network Redaction in Exported Reports
* **Description:** Aggressiveness of redacting discovered credentials and internal RFC 1918 IPs in exported reports.
* **Current Default:** Automated Pattern Redaction with Audit Toggle in report viewer.

---

## 6. Technical Risks & Mitigations Table (Updated for Revision 3.1)

| Risk ID | Category | Risk Description | Severity | Probability | Detection Strategy | Mitigation & Fallback Architecture |
|---|---|---|---|---|---|---|
| **RSK-001** | Security | Malicious target returns adversarial prompt injection in HTTP banner or web page body attempting to hijack LLM agent instructions. | **CRITICAL** | HIGH | Observation parser detects prompt delimiters; LLM response validation against schema. | Treat all tool output as untrusted data. Enforce structural separation in LLM message schemas (`role: tool` vs `role: system`). Model instructions strictly forbid taking operational instructions from observation data. |
| **RSK-002** | Security | Agent tool attempts to scan an out-of-scope IP due to open HTTP redirect or DNS rebinding. | **CRITICAL** | MEDIUM | Dual-homed Egress Gateway intercepts DNS and destination IP; audit log records blocked attempt. | Egress gateway resolves DNS independently and blocks any IP outside authorized CIDR/IP list. Target URLs are parsed and validated before tool command construction. |
| **RSK-003** | System | Concurrent terminal command execution by human operator conflicts with agent tool execution (file locking, resource starvation). | **HIGH** | MEDIUM | Concurrency manager monitors active PTY input and Docker Exec processes. | Distinct process namespaces; shared `/workspace` uses atomic writes; terminal status bar displays "Agent Active" badge; warning displayed if user executes commands during active agent step. |
| **RSK-004** | Durability | Sudden power outage, host crash, or OS kill (`SIGKILL`) during active investigation corrupts session state. | **HIGH** | LOW | Startup reconciliation checks SQLite WAL state and Docker container status. | SQLite WAL mode ensures zero file corruption. Checkpoints recorded at the end of each discrete action in Pi-inspired session state tree. Incomplete tasks marked `INTERRUPTED`. |
| **RSK-005** | Availability | Cloud LLM provider hits rate limits (HTTP 429), quota exhaustion, or prolonged outages during autonomous run. | **MEDIUM** | HIGH | Provider adapter catches HTTP 429/503; error streaming events emitted to UI. | Exponential backoff with jitter (up to 3 retries). If unrecoverable, agent auto-pauses investigation gracefully, saves checkpoint, and alerts user via UI notification. |
| **RSK-006** | Performance | Heavy network scanner (`nmap -p-` or `ffuf`) causes Kali container to exceed host memory, triggering OS OOM Killer. | **HIGH** | MEDIUM | Docker container health monitor polls memory usage; Docker events stream OOM event. | Container configured with hard memory ceiling (e.g., 2 GB) and CPU quota. Tools wrapped with internal rate-limiting and memory-efficient arguments. Agent detects OOM and marks task failed without crashing app. |
| **RSK-007** | Integrity | Stored API keys compromised via malware inspecting local application data files. | **HIGH** | LOW | Pre-flight security audit verifies storage backend; redacts secrets from memory. | Keys stored in OS Credential Manager (DPAPI on Windows, Keychain on macOS). Never written to disk in plaintext. Memory references zeroized after HTTP request generation. |
| **RSK-008** | Reporting | Script injection (XSS) in HTML report via crafted target server headers or vulnerability payloads. | **HIGH** | MEDIUM | Automated HTML report verification tests with fuzzing payloads in CI/CD pipeline. | Jinja2 autoescaping strictly enforced. Content Security Policy (`default-src 'none'`) embedded in HTML report. Raw evidence payloads encoded in base64 or plaintext code blocks. |
| **RSK-009** | Container | Orphaned Docker containers accumulate on the host after crashes or improper process termination. | **MEDIUM** | MEDIUM | Application startup sweep queries Docker daemon for containers with label `app=bletiner`. | Docker containers created with label `app=bletiner, session_id=<id>`. Startup reconciliation detects containers whose session is not running and offers cleanup or automatic recovery. |
| **RSK-010** | Concurrency | Long-running I/O or Docker API calls freeze the PySide6 desktop GUI thread. | **MEDIUM** | HIGH | QThread / asyncio profiling in CI; GUI frame rate monitoring. | Strict prohibition of blocking calls on main thread. All Docker API interactions and network I/O run asynchronously via `qasync` or `asyncio.to_thread`. |
| **RSK-011** | Context Budget | Excessive raw tool stdout (e.g. 50 MB nmap XML dump) exceeds LLM context window limits. | **HIGH** | HIGH | Context manager checks token count before adding tool observations to message history. | Tool output normalizer truncates raw output, saves full stream to filesystem evidence directory, and injects concise summary + relative artifact pointer into prompt context. |
| **RSK-012** | Tool Versioning | Pre-installed Kali tool syntax changes across Docker image updates, breaking parser regex. | **MEDIUM** | LOW | Tool execution integration tests run against mock command outputs in CI. | Docker image versions are strictly pinned; tool wrappers execute version verification commands (`nmap -V`, `nuclei -version`) during container initialization. |
| **RSK-013** | Security | Shared IP / Multi-tenant CDN Scope Escape: Target IP resolves to CDN hosting unauthorized co-tenants. | **CRITICAL** | HIGH | Gateway SNI / HTTP Host header inspector validates domain before forwarding packets. | Egress gateway intercepts TLS handshake SNI and HTTP Host header. Connections to unauthorized domains sharing the target's IP are dropped with TLS alert or HTTP 403. |
| **RSK-014** | Security | Tool command injection via crafted domain names or inputs passed through shell string concatenation. | **CRITICAL** | MEDIUM | Static linting rule forbidding `shell=True` or `bash -c` string formatting. | Mandatory structured argument arrays (`argv: list[str]`). All Docker Exec commands execute directly via Linux `execve` without subshell parsing. |
| **RSK-015** | Security | Blind HTTP CONNECT Tunnel Redirect Escape: Target redirects to out-of-scope domain over encrypted tunnel. | **CRITICAL** | HIGH | Two-tier redirect validation: client hook checks `Location`; gateway SNI checks new tunnel. | Layer A client hook sets `allow_redirects=False` and parses `Location` header before requesting. Layer B gateway drops out-of-scope SNI handshakes. |
| **RSK-016** | Security | Raw Socket Proxy Bypass: Non-proxy-aware tools (`nmap -sS`, raw UDP) bypass `HTTP_PROXY`. | **CRITICAL** | HIGH | Transparent default gateway routing on internal bridge + kernel IP forward packet filtering. | Default gateway points to `bletiner-gateway`. Gateway `iptables` FORWARD table drops packets to non-whitelisted IPs; DNAT intercepts port 53 DNS. |
| **RSK-017** | Execution | Non-root raw socket failure: `socket(AF_INET, SOCK_RAW)` fails under `no_new_privs: true`. | **HIGH** | HIGH | Pre-flight container capability test verifying `setpriv --ambient-caps`. | Entry point launches processes using Linux ambient capabilities (`cap_net_raw,cap_net_bind_service`), preserving raw socket permissions across `execve`. |
| **RSK-018** | Security | TLS Encrypted Client Hello (ECH) bypass: Client encrypts SNI using DNS HTTPS records, blinding gateway proxy. | **HIGH** | MEDIUM | Gateway DNS query monitor detecting type 65 HTTPS/SVCB responses. | Gateway internal DNS resolver intercepts DNS queries and strips type 65 HTTPS/SVCB records, forcing client to fall back to unencrypted TLS 1.3 SNI. |
| **RSK-019** | Network | Internal bridge missing default route: Kali container has zero routing to gateway container. | **HIGH** | HIGH | Route table check `ip route show default` during container initialization. | Host-side netns route injection via ephemeral setup container (`--network=container:bletiner-kali-<id>`) executes `ip route replace default via <gateway_ip>` at bootstrap. |

---

## 7. Trade-Off Analysis Matrix (Revision 3.1)

| Design Area | Selected Architecture | Alternative Considered | Key Advantages | Accepted Disadvantages / Trade-Offs | Rationale |
|---|---|---|---|---|---|
| **Default Routing** | Host-Side Netns Route Injection | Granting `CAP_NET_ADMIN` to Kali | Kali remains permanently unprivileged with zero network admin rights; routes traffic to gateway cleanly. | Requires running a 1-second ephemeral privileged setup container at startup. | Zero-trust container model; Kali must never possess capabilities to alter its own network routing (ADR-016). |
| **Target Routing** | Strict Separation (Domain vs IP) | Blanket IP Forwarding for all targets | Completely eliminates multi-tenant CDN co-tenant attacks for domain scans. | Cannot run raw SYN port scans against domain targets hosted behind CDNs. | Raw IP forwarding to multi-tenant CDNs is fundamentally insecure and escapes scope (ADR-018). |
| **ECH Handling** | DNS Type 65 HTTPS Stripping | Requiring TLS MITM CA Injection | Completely transparent; preserves authentic server certificates and scanner SSL checks. | Disables ECH for legitimate targets (acceptable for authorized security audits). | Preserves scanner TLS validation integrity while guaranteeing SNI visibility (ADR-017). |
| **Container Capabilities** | Non-Root User + Ambient Capabilities | Running as Root OR Granting `CAP_NET_ADMIN` | Unprivileged non-root execution (`bletiner`); retains raw sockets under `no_new_privs`; zero ability to modify firewall. | Requires using `setpriv` launcher to inherit ambient capabilities. | Least privilege principle; non-root user cannot compromise host or escape container. |
| **Network Topology** | Dual-Homed Bridge + Gateway Forwarding | Proxy-Only Routing OR Host iptables | Captures raw sockets, UDP, and non-proxy tools; 100% portable on Windows WSL2, macOS, Linux without host root. | Gateway container must act as router and forwarder with packet filtering. | Proxies alone are blind to raw TCP/UDP. Dual-homed bridge provides packet-level containment. |
| **Tool Safety Tiers** | 4-Tier Risk Model with Tier 4 Autonomous Block | Unrestricted Tool Invocation by Agent | Guarantees non-destructive operation; prevents denial of service and runaway network flooding. | Operator must run intrusive tools manually from terminal. | Autonomous AI must never execute unbounded exploit or DoS tools. |
| **Testing Verification** | Empirical Live Execution vs Doc Verification | Trusting documentation assertions | Prevents false sense of security; verifies fail-closed properties on live Docker engine. | Requires Docker environment to execute `scripts/verify_gateway_poc.py`. | "A security architecture is secure only after demonstrating it fails closed under adversarial tests." |

---

## 8. Document Sign-Off & Verification

This Register has been updated and re-verified in accordance with Master Prompt Sections 1, 2, and 8, the Pi-harness directives, and the **Revision 3.1 Technical Correction Review**. It provides complete, verified architectural boundaries and guarantees that no speculative requirements have been introduced without formal documentation and labeling. Claude Code must reference this document whenever evaluating architectural constraints, dependency selection, or security boundaries.
