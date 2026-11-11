# Bletiner AI --- Master Prompt for Product Documentation & Technical Specification

## Your role

You are a principal software architect, senior Python engineer,
desktop-application architect, AI-agent systems engineer, and
application-security engineer.

Your assignment is **not to implement the application yet**. Your
assignment is to produce a complete, internally consistent,
implementation-ready documentation package for **Bletiner AI**, an
autonomous security-investigation desktop application.

You must turn the product requirements below into a rigorous engineering
specification that another coding agent (Claude Code) can implement in
controlled phases.

Think critically. Do not blindly accept a design decision if it creates
a reliability, security, usability, or maintainability problem. Preserve
the product intent, identify the problem, explain the trade-off, and
recommend the safest practical design. Clearly distinguish: - Confirmed
product decisions. - Your recommended engineering decisions. - Open
questions that genuinely block implementation.

Do not invent requirements and present them as user-approved. Where a
detail is missing, make a conservative recommendation, label it as a
recommendation, and record it in an assumptions/open-decisions register.

------------------------------------------------------------------------

# 1. Product vision

**Product name:** Bletiner AI\
**Product type:** Python desktop application for autonomous, authorized
security investigation.\
**Primary interface:** PySide6 desktop application.\
**Execution environment:** Per-investigation Kali Linux Docker
container.\
**Agent design:** One autonomous agent using a Pi-inspired harness
architecture.\
**Primary outputs:** Evidence-backed Markdown and HTML investigation
reports, plus organized raw artifacts.

Bletiner AI should behave like an autonomous security investigator
working inside a controlled environment. A user supplies an authorized
target and scope. The agent plans and performs an investigation,
observes tool results, dynamically decides what to investigate next,
validates potential findings, records evidence, and produces reports.

The application is not intended to be a generic chatbot. It is an
investigation workspace with: - A live operational activity feed. - A
shared interactive Kali terminal. - A findings and evidence workspace. -
A session/history panel. - Human controls to pause, resume, stop, and
steer the agent. - Persistent investigation sessions. - Configurable
model providers and model discovery.

The agent should work quietly and autonomously. It should not narrate
every action in conversational prose. It should expose concise,
structured operational events and useful status updates.

------------------------------------------------------------------------

# 2. Confirmed product decisions

Treat the following as approved requirements unless a safety or
technical constraint makes a specific implementation impossible. If so,
explain the issue and propose the closest safe equivalent.

## 2.1 Technology direction

-   Primary implementation language: Python.
-   Desktop UI: PySide6.
-   Agent harness: adopt the useful architectural concepts of the Pi
    harness, independently implemented and adapted to this product.
-   Container orchestration: Docker, controlled through a Python
    integration.
-   Investigation environment: Kali Linux container.
-   Persistent application/session metadata: SQLite is the initial
    preferred choice.
-   Reports: Markdown (`.md`) and standalone HTML (`.html`).
-   The desktop application should launch when the user starts Bletiner
    AI from the terminal/command line.

Do not assume the Pi harness source code or its license permits copying.
Treat "Pi-inspired" as an architectural reference: study and adopt
appropriate concepts such as the agent loop, tool lifecycle,
message/context management, provider abstraction, event streaming,
steering, interruption, and session persistence. Document any external
code or dependency licensing review that would be required.

## 2.2 Main investigation workflow

The expected high-level workflow is:

1.  User creates a new investigation.
2.  User enters a target and authorized scope.
3.  Application validates the target format and scope.
4.  Application creates a dedicated investigation record and workspace.
5.  Application creates and configures that investigation's Kali Docker
    container.
6.  Agent initializes the investigation context and policy.
7.  Agent performs reconnaissance and service discovery.
8.  Agent analyzes observations and findings.
9.  Agent dynamically plans relevant follow-up tasks.
10. Agent executes allowed tools and checks inside the container.
11. Agent validates potential findings and collects evidence.
12. Agent prioritizes work based on severity, confidence, and
    investigation value.
13. Agent continues until completion criteria, limits, or stopping
    conditions are reached.
14. Agent generates Markdown and HTML reports.
15. All artifacts and session metadata are retained in the investigation
    folder.

The agent loop should be dynamic, not a fixed list of scanner commands.
Use the conceptual loop:

**Plan → Execute → Observe → Analyze → Re-plan**

The agent must not simply run every available tool. It should select
tools based on observed evidence, scope, policy, expected value, and
resource/time budgets.

## 2.3 Autonomy and human control

The default mode is autonomous investigation.

The user must be able to: - Pause an investigation. - Resume a paused
investigation. - Stop an investigation. - Send a new instruction while
the investigation is in progress (steering). - Inspect the current task
and recent activity. - Use the interactive Kali terminal. - Review
findings and artifacts. - Choose whether to continue with deeper
investigation after a Basic investigation.

Steering should update the agent's task direction without unnecessarily
destroying valid context or prior evidence. Define how the agent
incorporates a new instruction, validates it against scope and safety
policy, and records the change.

Pause, stop, and shutdown are different operations: - **Pause:** stop
scheduling new tasks and reach a safe checkpoint; handle an in-flight
process according to its cancellation policy. - **Stop:** terminate the
current investigation run deliberately, preserve results, and mark the
run stopped. - **Application exit:** gracefully stop agent execution,
stop the associated Docker container, persist state, and allow the user
to resume later.

Do not expose hidden chain-of-thought. The UI should show concise plans,
actions, tool invocations, observations, decisions, status transitions,
and evidence references. These operational records provide transparency
and auditability without presenting private internal reasoning.

## 2.4 Investigation depth

The user chooses the investigation depth when creating an investigation.

### Basic Investigation

Expected characteristics: - Reconnaissance and service discovery. -
Initial security checks. - Basic validation of potential findings. -
Evidence collection. - Preliminary findings and report.

### Deep Investigation

Expected characteristics: - Includes the Basic investigation work. -
Performs additional evidence-driven verification. - Correlates findings
across discovered services where relevant. - Investigates relevant
related attack surfaces within the approved scope. - Reduces false
positives through additional validation. - Collects stronger supporting
evidence. - Produces a more detailed report.

After a Basic investigation completes, the UI must offer the user the
option to start a Deep investigation using the existing session's
findings and evidence. It must not needlessly repeat completed work. The
system should identify what is reusable, what needs revalidation, and
what new tasks are being added.

Depth is a user choice, not a reason to bypass safety policy. Deep mode
must remain non-destructive by default.

## 2.5 One agent, modular internal responsibilities

Use a **single intelligent agent** for the initial product. Do not
introduce a multi-agent architecture in the first implementation.

Internally, keep clear modules/services for: - Planning. - Tool
selection. - Tool execution. - Observation parsing. - Finding
analysis. - Verification. - Prioritization. - Report generation.

These are modules or responsibilities controlled by one agent runtime,
not separate autonomous agents. Design interfaces so specialized agents
could be introduced in a future version without requiring a rewrite, but
do not build that complexity now.

## 2.6 Shared Kali Docker environment

Each investigation gets **one dedicated Kali Linux container**.

The agent and the user share that same container environment: - The
agent executes commands through its own controlled Docker Exec
session. - The user accesses an interactive terminal connected to the
same container. - Both see the same filesystem and workspace. -
User-created files can be inspected by the agent when relevant and
permitted. - Agent-created files can be inspected by the user. - Both
execution channels are subject to the investigation's network policy and
resource constraints. - All agent tool executions and relevant user
terminal activity should be auditable.

The user terminal must not grant access to the Docker daemon or host
filesystem. Never mount the Docker socket into the container. Avoid
privileged containers and unnecessary host mounts.

Design concurrency rules for the case where the user manually runs a
command while the agent is operating. Prevent conflicting writes and
make process ownership/status visible. Do not silently kill a user's
interactive process when the agent pauses.

## 2.7 Network restrictions and authorization

Use restricted networking as the default.

At the beginning of the application's first-use flow, present Terms and
Conditions / acceptable-use terms. The user must explicitly accept that
they will only investigate systems they own or are authorized to test.
Persist the acceptance version and timestamp. Do not ask the user to
repeatedly accept the same unchanged terms for every session. If terms
materially change, require renewed acceptance.

For every investigation, capture and enforce the actual target scope.
Supported target forms: - Domain names, e.g. `example.com`. - IP
addresses, e.g. `192.0.2.10`. - CIDR ranges, e.g. `192.0.2.0/24`. - Web
application URLs, e.g. `https://example.com`.

The scope model should account for DNS resolution, redirects,
subdomains, IP changes, ports, and related hosts. Do not assume that a
domain automatically authorizes every subdomain or every IP it resolves
to. Define safe, explicit scope expansion rules.

Network isolation must be enforced by infrastructure, not merely by
prompting the LLM: - Allow only explicitly authorized target
destinations and required infrastructure services. - Block access to
localhost, private/internal networks, link-local ranges, and host/Docker
control interfaces by default, unless an explicitly authorized and
separately configured lab/private target is supported. - Prevent
container access to cloud metadata endpoints and Docker control APIs. -
Avoid DNS rebinding and redirect-based scope escape. - Define how DNS
resolution and destination IP validation work. - Record network policy
decisions and relevant connection events where technically feasible. -
Require explicit user approval before expanding scope or enabling a more
permissive network policy.

Document the practical limitations of enforcing destination allowlists
at the Docker/network layer. Recommend a robust implementation approach,
such as a controlled egress proxy/firewall or equivalent host-enforced
network controls. Do not claim that a simple Docker bridge network alone
provides domain-level allowlisting.

## 2.8 Per-investigation isolation

The approved model is **one investigation = one dedicated container and
one independent workspace**.

Each investigation must have its own: - Docker container. - Container
lifecycle and configuration. - Network policy. - Workspace. - Agent
state/checkpoints. - Findings. - Evidence. - Logs. - Reports. -
Metadata.

Multiple investigations may run simultaneously. Each must remain
isolated from the others. Define CPU, memory, process, disk, timeout,
and concurrency limits. The application should expose resource usage and
handle resource exhaustion gracefully.

## 2.9 Application exit and resume

When the desktop application exits: 1. Stop scheduling new agent tasks.
2. Ask the agent runtime to reach a safe checkpoint. 3. Cancel or
terminate in-flight agent-owned commands according to a defined grace
period and escalation policy. 4. Do not leave autonomous investigation
work running in the background. 5. Stop the investigation's Docker
container. 6. Persist session state, messages/context needed for
continuation, task queue, task statuses, findings, evidence references,
logs, and report state. 7. Mark the session as paused/interrupted by
application shutdown. 8. On next launch, show resumable sessions.

When the user resumes: - Reconcile persisted state with actual
Docker/container state. - Recreate or restart the dedicated container
safely. - Revalidate the environment and network policy. - Restore the
agent from a durable checkpoint. - Inspect incomplete tasks and their
idempotency/retry status. - Do not blindly repeat a command that may
have completed before shutdown. - Require revalidation for stale
observations where appropriate.

Distinguish a durable investigation record from ephemeral container
processes. Do not promise that arbitrary running process memory can be
restored after container shutdown.

## 2.10 LLM providers and model locking

Implement a common provider adapter abstraction so the agent runtime is
independent of the model vendor.

Initial providers to plan for: - OpenAI. - Anthropic. - Google Gemini. -
DeepSeek. - Ollama. - LM Studio. - OpenRouter. - Groq. - Together AI. -
Mistral. - Custom OpenAI-compatible endpoints.

Use official APIs/SDKs where appropriate. Use provider-specific adapters
where API semantics differ. Do not force every provider through an
OpenAI-compatible protocol if doing so loses capabilities or causes
incompatibility.

A model is locked for the lifetime of an investigation session. Store: -
Provider identifier. - Model identifier. - Endpoint/base URL where
applicable. - Relevant non-secret configuration/version metadata. -
Capability profile used for the session. - Model selection timestamp.

Do not store API keys in plaintext in SQLite, reports, logs, or session
metadata. Recommend secure OS credential storage or an appropriate
secrets manager. Redact credentials from all logs and error messages.

If the selected model becomes unavailable when resuming, do not silently
switch models. Show a clear error and let the user explicitly select a
replacement. If the user confirms a replacement, record the model
transition in the audit history and explain that outputs may differ.

## 2.11 Model discovery scanner

The application must include a provider/model management area.

A user should be able to configure providers and request a
scan/discovery operation. The scanner should: - Query configured
providers for available model IDs where supported. - Discover local
models from Ollama and LM Studio where supported. - Check basic
connectivity and authentication. - Record provider-reported model
metadata. - Determine context-window information when available. -
Identify reported capabilities where available (tool/function calling,
streaming, vision, structured output). - Run controlled capability
probes where safe and appropriate. - Distinguish provider-reported
capabilities from empirically verified capabilities. - Show
unknown/unsupported values honestly rather than inventing them. - Allow
the user to select a model for a new investigation.

Capability tests should be lightweight, bounded, and avoid unnecessary
cost. Clearly warn before paid test calls if applicable. Do not
repeatedly probe models in the background without user consent.

## 2.12 Silent autonomous UI behaviour

The application should not behave like a chat-first assistant.

The agent works autonomously and displays concise structured events such
as: - Investigation initialized. - Container created. - Reconnaissance
started/completed. - Service discovered. - Tool
started/completed/failed. - Potential finding detected. - Verification
started/completed. - Finding confirmed/false positive/inconclusive. -
Follow-up task scheduled. - Investigation paused/resumed/stopped. -
Report generated.

The user can still send steering instructions through a compact
instruction input. This is a control mechanism, not a requirement for
the agent to narrate all its reasoning.

## 2.13 Finding prioritization and lifecycle

The agent should prioritize investigation work using: - Severity. -
Confidence. - Investigation value / expected information gain. -
Relevance to the approved target and scope. - Cost, time, and resource
consumption.

Do not silently discard low-priority findings. Keep a complete findings
queue/backlog with status and rationale for prioritization.

Define a finding lifecycle, at minimum: 1. Detected. 2. Needs triage. 3.
Under investigation. 4. Verified. 5. False positive. 6. Inconclusive. 7.
Accepted risk / informational, where applicable. 8. Included in report.

Each finding should have a stable ID and structured fields: - Title. -
Summary. - Affected asset/service. - Finding category. - Severity. -
Confidence. - Verification status. - Evidence references. - Detection
method/tool. - First seen and last checked timestamps. - Scope
reference. - Reproduction/validation notes, limited to safe authorized
checks. - Impact explanation. - Recommended remediation. - Related
findings. - Investigation history.

Severity must not be confused with confidence. A potentially severe
finding with low confidence should be clearly labelled as such. Define a
consistent severity and confidence scale.

## 2.14 Reporting and artifact storage

Every investigation must save a well-organized set of artifacts under
its own separate folder.

Proposed structure (adjust if you have a better, well-justified
structure):

``` text
Bletiner/
├── app/
├── config/
├── database/
└── investigations/
    ├── <session_id>/
    │   ├── workspace/
    │   ├── evidence/
    │   ├── findings/
    │   ├── logs/
    │   ├── checkpoints/
    │   ├── reports/
    │   │   ├── report.md
    │   │   └── report.html
    │   └── metadata.json
    └── ...
```

Generate both: - A readable, complete Markdown report. - A polished,
self-contained HTML report suitable for opening in a browser and
sharing.

Reports should include: - Executive summary. - Scope and authorization
statement. - Investigation mode (Basic/Deep). - Start/end times and
duration. - Methodology and limitations. - Assets and services
observed. - Findings summary. - Detailed finding descriptions. -
Severity and confidence. - Evidence references. - Validation status. -
Impact and remediation guidance. - Unverified/inconclusive items. -
Tools and relevant versions. - Relevant investigation timeline. - Clear
disclaimer about what was and was not tested.

Reports must not include secrets, API keys, session tokens, credentials,
or unnecessary sensitive data. Evidence should be referenced with stable
relative paths and hashes where appropriate. Preserve raw tool output
separately; do not rely solely on the LLM's summaries.

The HTML report should be generated safely. Escape untrusted tool output
and user-controlled strings to prevent HTML/script injection. Do not
load remote scripts or remote assets by default. Prefer a self-contained
report with local styling.

------------------------------------------------------------------------

# 3. Proposed desktop user experience

Use the supplied UI reference image if it is available in the working
context. Treat it as a layout/style reference, not a requirement to copy
every pixel.

The desktop application should have a professional, dark,
security-operations workspace aesthetic. Avoid a generic chat
application appearance.

## 3.1 Main layout

Use a three-region workspace:

### Left panel --- Investigation activity and findings

-   Current investigation status.
-   Structured activity/event feed.
-   Current phase and task.
-   Findings overview and severity counts.
-   Filters for events/findings.
-   Expandable event details, including tool name, status, timestamps,
    and artifact references.
-   Do not label private internal reasoning as "chain of thought."

### Center panel --- Main execution workspace

-   Main shared Kali terminal.
-   Clear container status.
-   Terminal connection status.
-   User can type and execute commands.
-   Agent activity can be inspected without making the terminal
    unusable.
-   Provide a compact steering input.
-   Pause, resume, and stop controls.
-   Show investigation progress and current high-level task.

### Right panel --- Sessions and documents

-   List of investigation sessions.
-   Session status and target.
-   Basic/Deep mode.
-   Model/provider used.
-   Generated reports.
-   Evidence and artifact browser.
-   Session resume/open controls.

The exact panel widths, responsive behaviour, keyboard navigation,
accessibility, themes, and component choices should be documented.

## 3.2 Terminal implementation

Research and recommend the most suitable way to embed a real interactive
terminal in PySide6. Evaluate options such as: - Qt-native terminal
approaches. - Qt WebEngine with xterm.js. - A local terminal bridge to a
PTY/terminal process inside Docker.

Do not assume a normal text widget is an adequate terminal emulator. The
solution must support interactive terminal behaviour, resizing, ANSI
colours, input, output streaming, and clean process lifecycle.

If using Qt WebEngine/xterm.js, define the secure local bridge protocol
and prevent arbitrary host command execution through the bridge. The
terminal bridge should only expose the authorized container session.

## 3.3 Accessibility and usability

Document: - Clear running/paused/stopped/error states. - Confirmation
for destructive actions. - Visible target/scope at all times. - Clear
separation between user-entered commands and agent-executed commands. -
Search/filter for logs and findings. - Copy/export actions. - Empty,
loading, and error states. - Safe handling of long-running operations. -
Keyboard shortcuts where useful. - UI responsiveness while scans run.

------------------------------------------------------------------------

# 4. Engineering architecture to design

Produce a detailed architecture with boundaries and responsibilities.
You may refine the package structure, but keep the system modular and
testable.

At minimum, cover these subsystems:

## 4.1 Desktop presentation layer

-   PySide6 application bootstrap.
-   Main window and panel composition.
-   View models/controllers or an equivalent clean UI pattern.
-   Qt signals/slots for background task updates.
-   Terminal widget/bridge.
-   Session and findings views.
-   Settings and provider configuration screens.
-   First-run Terms and Conditions acceptance screen.

Keep long-running work off the UI thread. Define how Python asyncio and
Qt event loops interact. Recommend a reliable concurrency model and
explain why.

## 4.2 Application/service layer

-   Investigation service.
-   Session service.
-   Container lifecycle service.
-   Agent orchestration service.
-   Provider/model management service.
-   Findings service.
-   Report service.
-   Settings/secrets service.
-   Authorization/scope service.

## 4.3 Agent harness

Design a Pi-inspired agent harness adapted for security investigation.
Specify: - Agent loop/state machine. - Provider adapter contract. -
Message representation. - Tool schema/registration. - Tool execution
lifecycle. - Tool result normalization. - Streaming event protocol. -
Context-window management. - History persistence. - Steering/injection
of new user instructions. - Pause and cancellation semantics. -
Retry/error policy. - Loop detection and duplicate-task prevention. -
Maximum iteration/task/time budgets. - Durable checkpoints. - Recovery
after process/application restart.

Use explicit typed interfaces and schemas. Explain which parts are
synchronous/asynchronous and where cancellation is supported.

Do not expose chain-of-thought. Define structured event types for plan
summaries, actions, observations, decisions, and status changes.

## 4.4 Tool system

Design a controlled tool registry and executor. Tools should have: -
Stable name and version. - Description. - Input schema. - Output
schema. - Risk classification. - Scope requirements. - Timeout/resource
policy. - Audit metadata. - Idempotency characteristics. - Cancellation
behaviour.

Separate: - Tool definitions. - Tool selection. - Tool execution. -
Output parsing. - Evidence extraction.

All security tools must execute in the per-session container. The host
application should not execute arbitrary target-scanning commands
directly.

Provide a tool lifecycle and a strategy for installing, pinning,
updating, and verifying tools in the Kali image. Do not assume every
tool should be installed at runtime. Prefer reproducible, version-pinned
images.

The system should support future tool additions without modifying the
core agent loop.

## 4.5 Docker/container subsystem

Document: - Image selection and maintenance. - Reproducible Dockerfile
strategy. - Container creation and labels. - Per-session
volumes/workspaces. - Non-root execution where practical. - Linux
capabilities minimization. - Read-only root filesystem where
compatible. - Temporary writable locations. - CPU/memory/PID/disk
limits. - Network restrictions. - No Docker socket mount. - No
privileged mode. - Container health checks. - Exec sessions for agent
and user. - Container stop/remove/recreate behaviour. - Image versioning
and migration. - Cleanup of abandoned containers. - Crash recovery and
reconciliation. - Secure handling of mounted paths.

Explain what persists after a container is stopped and how the workspace
is mounted or otherwise preserved. Ensure one investigation cannot read
another investigation's data.

## 4.6 Scope and network enforcement

Design an explicit scope object and enforcement layer. Include: - Target
normalization. - Domain/IP/CIDR/URL parsing. - DNS resolution. -
Redirect handling. - Subdomain rules. - Port/protocol restrictions if
appropriate. - Destination IP validation. - Private, loopback,
link-local, multicast, and reserved address handling. - DNS rebinding
protections. - Egress proxy/firewall approach. - Host and Docker
control-plane protection. - Audit events for allowed/blocked
connections. - User-approved scope changes.

Be precise about what can and cannot be enforced at each layer. Do not
claim that an application-level check alone prevents network escape.

## 4.7 Session persistence and recovery

Define the data model and lifecycle for: - Investigation. - Session. -
Agent run. - Task. - Tool execution. - Message/context record. -
Finding. - Evidence artifact. - Report. - Container. - Checkpoint. -
User steering instruction. - Terms acceptance. - Provider/model
configuration reference.

Provide a state machine for session lifecycle, such as: - Draft. -
Awaiting setup. - Initializing. - Running. - Pausing. - Paused. -
Resuming. - Completed. - Stopping. - Stopped. - Failed.

Define legal transitions and recovery behaviour. Use transactions and
atomic file operations where appropriate. Explain how SQLite and
filesystem artifacts remain consistent after crashes.

## 4.8 Provider and model subsystem

Define: - Common provider interface. - Provider-specific adapters. -
Streaming response contract. - Tool/function calling normalization. -
Error normalization. - Authentication and secrets. - Model discovery. -
Capability probing. - Model lock per session. - Model replacement on
resume with explicit user confirmation. - Provider rate limits and retry
handling. - Usage/cost visibility where available.

Include a provider capability matrix, but mark unknown or variable
capabilities honestly. Do not fabricate model availability or fixed
context windows.

## 4.9 Findings and investigation prioritization

Define a transparent prioritization method that combines severity,
confidence, and investigation value. A scoring model may be proposed,
but: - Explain each factor. - Avoid false precision. - Keep the
underlying factors visible. - Allow safety policy and scope to override
priority. - Retain lower-priority findings in the queue. - Ensure
findings are not silently discarded.

Define verification standards and evidence requirements for each finding
status.

## 4.10 Reporting subsystem

Define report schemas, templates, generation timing, report versioning,
and safe HTML generation. Ensure both Markdown and HTML derive from the
same structured investigation data so they do not contradict each other.

------------------------------------------------------------------------

# 5. Safety, security, and reliability requirements

This is a security product, so safety controls must be part of the
architecture, not an afterthought.

The default investigation policy should be non-destructive and limited
to authorized targets.

Do not design the agent to: - Break into systems. - Steal credentials or
tokens. - Exfiltrate data. - Persist access. - Deploy malware or
destructive payloads. - Modify or delete target data. - Disrupt
services. - Perform denial-of-service or stress testing. - Evade
detection or bypass security controls. - Expand beyond the explicitly
authorized scope.

The agent may perform bounded, non-destructive reconnaissance and
validation appropriate to the selected mode and authorization. More
intrusive tests must be blocked by default and require explicit,
specific authorization and a safe implementation policy. If an operation
cannot be made safe or reliably scoped, it must not be automated.

Document: - Threat model. - Host/container boundary. - Network threat
model. - Prompt injection risks from web pages, banners, files, and tool
output. - Untrusted-data handling. - Secrets management. - Report
injection risks. - Path traversal protections. - Command
construction/injection protections. - Dependency and image supply-chain
risks. - Audit log integrity. - Resource exhaustion. - Crash recovery. -
Concurrent terminal/agent execution. - Safe shutdown. - User consent and
authorization records.

Treat all target-derived content and tool output as untrusted data,
never as instructions that can override system policy, scope, or tool
permissions.

------------------------------------------------------------------------

# 6. Testing and quality strategy

Create a comprehensive testing plan.

Include: - Unit tests for every core subsystem. - Provider adapter
contract tests with mocked APIs. - Model scanner tests. - Agent loop
tests with deterministic fake models. - Tool registry/executor tests. -
Scope parser and network policy tests. - Docker lifecycle integration
tests. - Shared terminal tests. - Session persistence/recovery tests. -
Shutdown/resume tests. - Basic-to-Deep continuation tests. - Finding
prioritization and status tests. - Markdown/HTML report consistency
tests. - HTML injection tests. - Path traversal and workspace isolation
tests. - UI tests for critical workflows. - End-to-end tests using a
deliberately vulnerable local lab target only. - Failure injection tests
for Docker crashes, provider timeouts, application termination,
disk-full, and malformed tool output.

Use mocks/fakes for most tests. Do not run tests against public systems.
Provide a safe local lab test strategy.

Define acceptance criteria for: 1. First launch and terms acceptance. 2.
Provider configuration and model discovery. 3. New investigation
creation. 4. Scope enforcement. 5. Container startup. 6. Shared
user/agent terminal. 7. Autonomous Basic investigation. 8. Dynamic
follow-up investigation. 9. Finding prioritization and verification. 10.
Steering, pause, and resume. 11. Basic-to-Deep continuation. 12.
Multiple concurrent sessions. 13. Graceful application exit. 14.
Recovery after restart. 15. Markdown and HTML report generation. 16.
Session deletion/cleanup with confirmation.

------------------------------------------------------------------------

# 7. Performance and operational requirements

Recommend measurable initial targets for: - UI responsiveness. - Event
update latency. - Session startup. - Container startup. - Log
throughput. - Memory use of the desktop app. - Number of concurrent
investigations. - Disk usage and retention. - Report generation. -
Cancellation/shutdown time.

Mark targets as proposed until validated on representative hardware. The
application should degrade gracefully when the host cannot support
multiple concurrent containers.

Document logging levels, structured logs, correlation IDs, per-session
tracing, diagnostic bundles, and privacy-aware telemetry. Do not add
cloud telemetry by default without explicit user consent.

------------------------------------------------------------------------

# 8. Required documentation deliverables

Produce a complete documentation package, not one short overview. Use a
`docs/` directory and create the following files. If you have access to
a repository, inspect it first and adapt the documentation to the actual
codebase. If no repository exists, document the proposed architecture
and clearly mark it as proposed.

## Required files

### `docs/00_PRODUCT_OVERVIEW.md`

-   Product vision.
-   Target users and use cases.
-   Core workflows.
-   Confirmed requirements.
-   Out-of-scope capabilities.
-   Product principles.
-   Glossary.

### `docs/01_REQUIREMENTS.md`

-   Functional requirements with stable IDs (FR-001, etc.).
-   Non-functional requirements with stable IDs (NFR-001, etc.).
-   Security and safety requirements with stable IDs (SEC-001, etc.).
-   User stories.
-   Acceptance criteria.
-   Requirement traceability matrix.

### `docs/02_SYSTEM_ARCHITECTURE.md`

-   System context diagram.
-   Container/deployment diagram.
-   Component architecture.
-   Runtime architecture.
-   Trust boundaries.
-   Data flows.
-   Key design decisions and rationale.
-   Architecture Decision Records (ADRs).

Use Mermaid diagrams where useful. Ensure diagrams are syntactically
valid.

### `docs/03_AGENT_HARNESS.md`

-   Pi-inspired harness concepts to adopt.
-   Agent loop and state machine.
-   Planning/execution/observation/replanning.
-   Tool lifecycle.
-   Streaming events.
-   Steering.
-   Cancellation and pause.
-   Context management.
-   Checkpointing and recovery.
-   Loop prevention and budgets.

### `docs/04_DOCKER_AND_NETWORK_SECURITY.md`

-   Kali image strategy.
-   Per-investigation container design.
-   Shared terminal design.
-   Container hardening.
-   Network policy.
-   Scope enforcement.
-   Egress control.
-   Threat model.
-   Safe cleanup and recovery.

### `docs/05_DESKTOP_UI_UX.md`

-   PySide6 architecture.
-   Main workspace layout.
-   Left/center/right panel specifications.
-   Terminal interaction.
-   Session navigation.
-   Findings and artifacts views.
-   Settings/provider screens.
-   Wireframes using Mermaid or text where appropriate.
-   UI state definitions and interaction flows.

### `docs/06_LLM_PROVIDERS_AND_MODEL_DISCOVERY.md`

-   Provider adapter interface.
-   Provider list and integration strategy.
-   Model discovery scanner.
-   Capability verification.
-   Model locking.
-   Secrets handling.
-   Provider failure and retry policies.

### `docs/07_DATA_MODEL_AND_SESSION_LIFECYCLE.md`

-   Entity definitions.
-   SQLite schema proposal.
-   Relationships and indexes.
-   Filesystem artifact model.
-   Session state machine.
-   Shutdown/resume recovery.
-   Data migration/versioning.

### `docs/08_FINDINGS_AND_REPORTING.md`

-   Finding schema.
-   Severity/confidence scales.
-   Prioritization model.
-   Verification lifecycle.
-   Evidence handling.
-   Markdown report specification.
-   HTML report specification.
-   Report examples or templates.

### `docs/09_SECURITY_THREAT_MODEL.md`

-   Assets.
-   Threat actors.
-   Trust boundaries.
-   Attack surfaces.
-   Abuse cases.
-   Mitigations.
-   Residual risks.
-   Security assumptions.

### `docs/10_TESTING_AND_ACCEPTANCE.md`

-   Testing pyramid.
-   Unit/integration/UI/E2E plans.
-   Safe lab environment.
-   Failure injection.
-   Acceptance criteria.
-   Release gates.

### `docs/11_IMPLEMENTATION_ROADMAP.md`

-   Phased implementation plan.
-   Dependencies between phases.
-   Deliverables per phase.
-   Exit criteria.
-   Risks.
-   Suggested milestones.
-   What must be proven before proceeding.

### `docs/12_PROJECT_STRUCTURE.md`

-   Recommended repository structure.
-   Module responsibilities.
-   Dependency direction.
-   Import boundaries.
-   Configuration layout.
-   Test layout.
-   Naming conventions.

### `docs/13_OPEN_DECISIONS_AND_ASSUMPTIONS.md`

-   Confirmed decisions.
-   Engineering recommendations.
-   Assumptions.
-   Open questions.
-   Risks and trade-offs.
-   Decisions requiring user approval.

### `docs/14_CLAUDE_CODE_IMPLEMENTATION_BRIEF.md`

This is especially important. Create a concise but technically complete
handoff for Claude Code: - What to build. - Architecture boundaries. -
Required implementation order. - Coding standards. - Testing
expectations. - Security rules. - What not to implement prematurely. -
How to report progress. - Required phase gates. - Conditions under which
Claude Code must stop and ask rather than guessing.

Also create: - `docs/README.md` --- documentation index and recommended
reading order. - `docs/TRACEABILITY_MATRIX.md` --- map requirements to
architecture components, implementation phases, tests, and acceptance
criteria.

------------------------------------------------------------------------

# 9. Documentation quality requirements

The documentation must be: - Detailed enough to implement from. -
Internally consistent. - Explicit about data contracts and lifecycle
transitions. - Clear about host vs container responsibilities. - Clear
about UI vs background execution. - Clear about agent vs user terminal
execution. - Clear about persistent vs ephemeral state. - Clear about
what is enforced by code, Docker, host networking, and user consent. -
Honest about limitations and provider capability variability. - Free of
fabricated benchmarks, API behaviours, or guarantees.

Use tables for comparisons and structured requirements. Use Mermaid for
architecture/state/data-flow diagrams where it improves clarity. Include
examples of JSON schemas or Python protocols when they materially
clarify interfaces, but do not write the complete application code.

Avoid vague statements such as "secure the container" or "handle
errors." Specify what is protected, how, where, and how it is tested.

Every requirement should have a stable ID and a testable acceptance
criterion. Maintain traceability across requirements, design,
implementation phases, and tests.

When recommending a library, explain: - Why it fits. - Important
alternatives. - Compatibility risks. - Maintenance/security
considerations. - Whether it is mandatory or optional.

Do not add frameworks or dependencies just because they are popular.
Prefer a small, maintainable dependency set.

------------------------------------------------------------------------

# 10. Research and repository inspection

If internet research is available, verify current official documentation
for: - Python version compatibility. - PySide6 and Qt WebEngine
compatibility. - Docker Engine/SDK behaviour. - Provider APIs and
official SDKs. - Ollama and LM Studio APIs. - SQLite durability and
concurrency considerations. - Terminal emulator integration. - Relevant
security hardening practices.

Prefer official documentation and primary sources. Record access date
and links in a references section. Do not make up citations.

If working inside an existing repository: 1. Inspect the complete
directory tree. 2. Read existing source code, configuration, tests, and
README. 3. Identify existing functionality worth retaining. 4. Identify
obsolete or conflicting architecture. 5. Record findings in the
documentation. 6. Do not modify application source code as part of this
documentation-only task unless explicitly requested.

If the repository is empty, say so and produce a proposed design.

------------------------------------------------------------------------

# 11. Working method

Follow this sequence:

1.  Inspect repository and supplied context.
2.  Extract confirmed requirements.
3.  Identify contradictions, technical risks, and missing details.
4.  Establish explicit assumptions.
5.  Design the architecture and trust boundaries.
6.  Design the agent loop and tool lifecycle.
7.  Design Docker, shared terminal, and network isolation.
8.  Design persistence, shutdown, and resume.
9.  Design providers and model discovery.
10. Design findings and reports.
11. Design the PySide6 UI.
12. Build the testing and acceptance strategy.
13. Create the phased implementation roadmap.
14. Create the Claude Code handoff.
15. Cross-check every document for consistency and traceability.

Do not stop after writing an outline. Produce all required documentation
files with meaningful content.

Do not start implementing the application. Do not generate a large
amount of application source code. This task is documentation and
architecture specification only.

If a truly blocking question arises, ask it before finalizing.
Otherwise, make a conservative recommendation, document it as an
assumption, and continue. Do not interrupt the task for minor
preferences.

------------------------------------------------------------------------

# 12. Final quality gate

Before declaring documentation complete, perform a documentation audit.

Verify: - All required files exist. - No confirmed decision has been
contradicted. - Every functional requirement has an ID. - Every critical
requirement has acceptance criteria. - Every major component has defined
responsibilities. - The agent loop has explicit states and
transitions. - Pause, stop, shutdown, and resume are clearly
distinguished. - Shared terminal and agent execution concurrency are
addressed. - Each investigation has an independent container and
workspace. - Network enforcement is not falsely attributed to the LLM. -
Target scope cannot be silently expanded. - Model locking and
replacement are explicit. - API keys and secrets are never stored in
plaintext. - Findings are evidence-backed and lower-priority findings
remain tracked. - Basic-to-Deep continuation reuses prior work safely. -
Markdown and HTML reports derive from consistent structured data. -
Untrusted output is safely handled in the UI and reports. - Testing
includes failure and recovery scenarios. - The implementation roadmap
has verifiable phase gates. - Claude Code has enough detail to begin
implementation without inventing the architecture.

Create a final audit document:

`docs/15_DOCUMENTATION_AUDIT.md`

It must list each check as PASS, FAIL, or NEEDS REVIEW, with references
to the relevant documentation sections. Fix documentation
inconsistencies before marking the package complete.

At the end, provide a concise completion summary: - Files created. - Key
architectural decisions. - Assumptions made. - Remaining blockers. -
Recommended first implementation phase.

**The deliverable is a complete, coherent engineering documentation
package that can serve as the source of truth for implementing Bletiner
AI.**
