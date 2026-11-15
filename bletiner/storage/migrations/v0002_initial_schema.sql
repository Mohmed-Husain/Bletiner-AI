-- Bletiner AI schema v2, extracted verbatim from docs/07_DATA_MODEL_AND_SESSION_LIFECYCLE.md section 3.1.
-- Applied by bletiner.storage.database.Database.migrate() inside one exclusive transaction.
-- ============================================================================
-- Bletiner AI Database Schema: Production Specification (Revision 2)
-- Dialect: SQLite 3.37+
-- Conventions: UUID primary keys, UTC ISO-8601 strings, Strict Foreign Keys
-- Privacy: Zero Personally Identifiable Information (PII) stored in database
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 1. Legal Terms & Acceptable Use Acceptance (Privacy-Minimized)
-- ----------------------------------------------------------------------------
CREATE TABLE terms_acceptance (
    acceptance_id       TEXT PRIMARY KEY NOT NULL CHECK(length(acceptance_id) = 36),
    terms_version       TEXT NOT NULL,
    terms_hash          TEXT NOT NULL CHECK(length(terms_hash) = 64),      -- SHA-256 of terms text
    accepted_at         TEXT NOT NULL,                                     -- ISO-8601 UTC timestamp
    acceptance_hash     TEXT NOT NULL CHECK(length(acceptance_hash) = 64)  -- SHA-256(terms_hash + accepted_at)
);

CREATE UNIQUE INDEX uq_terms_acceptance_version 
    ON terms_acceptance (terms_version);

-- ----------------------------------------------------------------------------
-- 2. LLM Provider & Model Configurations
-- ----------------------------------------------------------------------------
CREATE TABLE provider_model_config (
    config_id           TEXT PRIMARY KEY NOT NULL CHECK(length(config_id) = 36),
    provider_name       TEXT NOT NULL, -- e.g., 'anthropic', 'openai', 'ollama', 'gemini'
    model_identifier    TEXT NOT NULL, -- e.g., 'claude-3-5-sonnet-20241022'
    display_name        TEXT NOT NULL,
    base_url            TEXT,          -- NULL for official cloud endpoints, URL for local/custom
    keyring_service_key TEXT NOT NULL, -- Key used to retrieve secret from OS credential store
    context_window_size INTEGER NOT NULL CHECK(context_window_size > 0),
    max_output_tokens   INTEGER NOT NULL CHECK(max_output_tokens > 0),
    supports_tool_call  INTEGER NOT NULL CHECK(supports_tool_call IN (0, 1)),
    supports_vision     INTEGER NOT NULL CHECK(supports_vision IN (0, 1)),
    supports_streaming  INTEGER NOT NULL CHECK(supports_streaming IN (0, 1)),
    is_active           INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX idx_provider_model_lookup 
    ON provider_model_config (provider_name, model_identifier, is_active);

-- ----------------------------------------------------------------------------
-- 3. Investigations (Engagement Root)
-- ----------------------------------------------------------------------------
CREATE TABLE investigations (
    investigation_id    TEXT PRIMARY KEY NOT NULL CHECK(length(investigation_id) = 36),
    title               TEXT NOT NULL,
    description         TEXT,
    primary_target      TEXT NOT NULL,
    investigation_depth TEXT NOT NULL CHECK(investigation_depth IN ('BASIC', 'DEEP')),
    status              TEXT NOT NULL CHECK(status IN ('DRAFT', 'ACTIVE', 'PAUSED', 'COMPLETED', 'TERMINATED', 'FAILED')),
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    completed_at        TEXT,
    terms_acceptance_id TEXT NOT NULL,
    FOREIGN KEY (terms_acceptance_id) REFERENCES terms_acceptance (acceptance_id) ON DELETE RESTRICT
);

CREATE INDEX idx_investigations_status ON investigations (status);
CREATE INDEX idx_investigations_created ON investigations (created_at DESC);

-- ----------------------------------------------------------------------------
-- 4. Scope Targets (Explicit Target Authorization)
-- ----------------------------------------------------------------------------
CREATE TABLE scope_targets (
    target_id           TEXT PRIMARY KEY NOT NULL CHECK(length(target_id) = 36),
    investigation_id    TEXT NOT NULL,
    target_type         TEXT NOT NULL CHECK(target_type IN ('DOMAIN', 'IPV4', 'IPV6', 'CIDR_V4', 'CIDR_V6', 'URL')),
    target_value        TEXT NOT NULL,
    allow_subdomains    INTEGER NOT NULL DEFAULT 0 CHECK(allow_subdomains IN (0, 1)),
    allowed_ports       TEXT, -- JSON Array of integers or port ranges, e.g., '[80, 443, 8000-8080]'
    is_excluded         INTEGER NOT NULL DEFAULT 0 CHECK(is_excluded IN (0, 1)),
    validation_status   TEXT NOT NULL CHECK(validation_status IN ('VALID', 'INVALID', 'BLOCKED')),
    created_at          TEXT NOT NULL,
    FOREIGN KEY (investigation_id) REFERENCES investigations (investigation_id) ON DELETE CASCADE
);

CREATE INDEX idx_scope_targets_inv ON scope_targets (investigation_id);

-- ----------------------------------------------------------------------------
-- 5. Sessions (Runtime Execution Contexts)
-- ----------------------------------------------------------------------------
CREATE TABLE sessions (
    session_id          TEXT PRIMARY KEY NOT NULL CHECK(length(session_id) = 36),
    investigation_id    TEXT NOT NULL,
    provider_config_id  TEXT NOT NULL,
    session_status      TEXT NOT NULL CHECK(session_status IN (
                            'DRAFT', 'AWAITING_SETUP', 'INITIALIZING', 'RUNNING',
                            'PAUSING', 'PAUSED', 'RESUMING', 'COMPLETED',
                            'STOPPING', 'STOPPED', 'FAILED'
                        )),
    relative_path       TEXT NOT NULL, -- e.g., 'investigations/8f1c4...'
    total_tokens_used   INTEGER NOT NULL DEFAULT 0,
    total_cost_usd      REAL NOT NULL DEFAULT 0.0,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    ended_at            TEXT,
    FOREIGN KEY (investigation_id) REFERENCES investigations (investigation_id) ON DELETE CASCADE,
    FOREIGN KEY (provider_config_id) REFERENCES provider_model_config (config_id) ON DELETE RESTRICT
);

CREATE INDEX idx_sessions_investigation ON sessions (investigation_id);
CREATE INDEX idx_sessions_status ON sessions (session_status);

-- ----------------------------------------------------------------------------
-- 6. Containers (Kali Container Lifecycle & Allocation)
-- ----------------------------------------------------------------------------
CREATE TABLE containers (
    container_id        TEXT PRIMARY KEY NOT NULL CHECK(length(container_id) = 36),
    session_id          TEXT NOT NULL UNIQUE,
    docker_container_id TEXT UNIQUE,
    image_name          TEXT NOT NULL,
    image_digest        TEXT NOT NULL,
    container_name      TEXT NOT NULL UNIQUE,
    ip_address          TEXT,
    network_mode        TEXT NOT NULL DEFAULT 'isolated-bridge',
    status              TEXT NOT NULL CHECK(status IN ('ALLOCATED', 'CREATING', 'RUNNING', 'STOPPED', 'RESTARTING', 'DESTROYED', 'ORPHANED')),
    cpu_limit           REAL NOT NULL DEFAULT 2.0,
    memory_limit_mb     INTEGER NOT NULL DEFAULT 4096,
    created_at          TEXT NOT NULL,
    started_at          TEXT,
    stopped_at          TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions (session_id) ON DELETE CASCADE
);

CREATE INDEX idx_containers_docker_id ON containers (docker_container_id);

-- ----------------------------------------------------------------------------
-- 7. Agent Runs (Discrete Autonomous Execution Epochs)
-- ----------------------------------------------------------------------------
CREATE TABLE agent_runs (
    run_id              TEXT PRIMARY KEY NOT NULL CHECK(length(run_id) = 36),
    session_id          TEXT NOT NULL,
    run_epoch           INTEGER NOT NULL DEFAULT 1,
    run_depth           TEXT NOT NULL CHECK(run_depth IN ('BASIC', 'DEEP')),
    status              TEXT NOT NULL CHECK(status IN ('INITIALIZING', 'RUNNING', 'PAUSING', 'PAUSED', 'COMPLETED', 'TERMINATED', 'FAILED')),
    start_reason        TEXT NOT NULL CHECK(start_reason IN ('INITIAL_START', 'USER_RESUME', 'STEERING_RESUME', 'DEPTH_UPGRADE')),
    exit_reason         TEXT,
    active_checkpoint_id TEXT,
    started_at          TEXT NOT NULL,
    ended_at            TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions (session_id) ON DELETE CASCADE
);

CREATE INDEX idx_agent_runs_session ON agent_runs (session_id, run_epoch);

-- ----------------------------------------------------------------------------
-- 8. Checkpoints (Serialized Agent Memory & State Snapshots)
-- ----------------------------------------------------------------------------
CREATE TABLE checkpoints (
    checkpoint_id       TEXT PRIMARY KEY NOT NULL CHECK(length(checkpoint_id) = 36),
    run_id              TEXT NOT NULL,
    sequence_number     INTEGER NOT NULL,
    checkpoint_trigger  TEXT NOT NULL CHECK(checkpoint_trigger IN ('PERIODIC', 'PRE_TOOL', 'POST_TOOL', 'PRE_PAUSE', 'SHUTDOWN')),
    relative_filepath   TEXT NOT NULL, -- JSON snapshot relative path
    sha256_hash         TEXT NOT NULL,
    context_tokens      INTEGER NOT NULL,
    active_task_id      TEXT,
    created_at          TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES agent_runs (run_id) ON DELETE CASCADE
);

CREATE INDEX idx_checkpoints_run_seq ON checkpoints (run_id, sequence_number DESC);

-- ----------------------------------------------------------------------------
-- 9. Steering Instructions (Human-In-The-Loop Directives)
-- ----------------------------------------------------------------------------
CREATE TABLE steering_instructions (
    instruction_id      TEXT PRIMARY KEY NOT NULL CHECK(length(instruction_id) = 36),
    session_id          TEXT NOT NULL,
    run_id              TEXT NOT NULL,
    instruction_text    TEXT NOT NULL,
    validation_status   TEXT NOT NULL CHECK(validation_status IN ('PENDING', 'ACCEPTED', 'REJECTED_SCOPE', 'REJECTED_SAFETY')),
    rejection_reason    TEXT,
    applied_status      TEXT NOT NULL CHECK(applied_status IN ('QUEUED', 'INCORPORATED', 'DISCARDED')),
    created_at          TEXT NOT NULL,
    incorporated_at     TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions (session_id) ON DELETE CASCADE,
    FOREIGN KEY (run_id) REFERENCES agent_runs (run_id) ON DELETE CASCADE
);

CREATE INDEX idx_steering_session ON steering_instructions (session_id, applied_status);

-- ----------------------------------------------------------------------------
-- 10. Tasks (Agent Task Graph & Queue)
-- ----------------------------------------------------------------------------
CREATE TABLE tasks (
    task_id             TEXT PRIMARY KEY NOT NULL CHECK(length(task_id) = 36),
    run_id              TEXT NOT NULL,
    parent_task_id      TEXT,
    task_name           TEXT NOT NULL,
    task_type           TEXT NOT NULL CHECK(task_type IN ('RECON', 'DISCOVERY', 'PROBE', 'VERIFY', 'ANALYZE', 'REPORT')),
    target_asset        TEXT NOT NULL,
    parameters_json     TEXT NOT NULL,
    priority_score      REAL NOT NULL DEFAULT 50.0,
    status              TEXT NOT NULL CHECK(status IN ('PENDING', 'SCHEDULED', 'RUNNING', 'COMPLETED', 'BLOCKED', 'FAILED', 'CANCELLED')),
    is_idempotent       INTEGER NOT NULL DEFAULT 1 CHECK(is_idempotent IN (0, 1)),
    retry_count         INTEGER NOT NULL DEFAULT 0,
    max_retries         INTEGER NOT NULL DEFAULT 2,
    failure_reason      TEXT,
    created_at          TEXT NOT NULL,
    started_at          TEXT,
    completed_at        TEXT,
    FOREIGN KEY (run_id) REFERENCES agent_runs (run_id) ON DELETE CASCADE,
    FOREIGN KEY (parent_task_id) REFERENCES tasks (task_id) ON DELETE SET NULL
);

CREATE INDEX idx_tasks_run_status ON tasks (run_id, status, priority_score DESC);

-- ----------------------------------------------------------------------------
-- 11. Tool Executions (Container Command Invocations)
-- ----------------------------------------------------------------------------
CREATE TABLE tool_executions (
    execution_id        TEXT PRIMARY KEY NOT NULL CHECK(length(execution_id) = 36),
    task_id             TEXT NOT NULL,
    container_id        TEXT NOT NULL,
    tool_name           TEXT NOT NULL, -- e.g., 'nmap', 'ffuf', 'nikto', 'sslyze'
    command_line        TEXT NOT NULL, -- Sanitized raw command executed
    working_directory   TEXT NOT NULL DEFAULT '/workspace',
    timeout_seconds     INTEGER NOT NULL DEFAULT 300,
    exit_code           INTEGER,
    status              TEXT NOT NULL CHECK(status IN ('QUEUED', 'RUNNING', 'SUCCESS', 'FAILED', 'TIMED_OUT', 'CANCELLED')),
    stdout_artifact_id  TEXT,
    stderr_artifact_id  TEXT,
    stdout_bytes        INTEGER DEFAULT 0,
    stderr_bytes        INTEGER DEFAULT 0,
    parsed_summary_json TEXT,
    started_at          TEXT NOT NULL,
    completed_at        TEXT,
    FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE,
    FOREIGN KEY (container_id) REFERENCES containers (container_id) ON DELETE RESTRICT
);

CREATE INDEX idx_tool_exec_task ON tool_executions (task_id);
CREATE INDEX idx_tool_exec_status ON tool_executions (status);

-- ----------------------------------------------------------------------------
-- 12. Message Records (Harness Context & Interaction Log)
-- ----------------------------------------------------------------------------
CREATE TABLE message_records (
    message_id          TEXT PRIMARY KEY NOT NULL CHECK(length(message_id) = 36),
    run_id              TEXT NOT NULL,
    sequence_number     INTEGER NOT NULL,
    role                TEXT NOT NULL CHECK(role IN ('system', 'user', 'assistant', 'tool')),
    content_text        TEXT,
    tool_calls_json     TEXT, -- Encoded function/tool invocation schemas
    tool_call_id        TEXT, -- For role='tool', ties back to LLM tool call ID
    token_count         INTEGER NOT NULL DEFAULT 0,
    cost_usd            REAL NOT NULL DEFAULT 0.0,
    is_evicted_from_ctx INTEGER NOT NULL DEFAULT 0 CHECK(is_evicted_from_ctx IN (0, 1)),
    created_at          TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES agent_runs (run_id) ON DELETE CASCADE
);

CREATE INDEX idx_message_run_seq ON message_records (run_id, sequence_number ASC);

-- ----------------------------------------------------------------------------
-- 13. Findings (Identified Vulnerabilities & Observations)
-- ----------------------------------------------------------------------------
CREATE TABLE findings (
    finding_id          TEXT PRIMARY KEY NOT NULL CHECK(length(finding_id) = 36),
    investigation_id    TEXT NOT NULL,
    session_id          TEXT NOT NULL,
    task_id             TEXT,
    stable_finding_key  TEXT NOT NULL, -- Deterministic deduplication hash: sha256(asset + category + port)
    title               TEXT NOT NULL,
    summary             TEXT NOT NULL,
    category            TEXT NOT NULL, -- e.g., 'INSECURE_TLS', 'EXPOSED_SERVICE', 'INJECTION'
    affected_asset      TEXT NOT NULL, -- Host, IP, or URL
    affected_port       INTEGER,
    severity            TEXT NOT NULL CHECK(severity IN ('INFO', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    confidence          TEXT NOT NULL CHECK(confidence IN ('LOW', 'MEDIUM', 'HIGH', 'CONFIRMED')),
    verification_status TEXT NOT NULL CHECK(verification_status IN (
                            'DETECTED', 'NEEDS_TRIAGE', 'UNDER_INVESTIGATION',
                            'VERIFIED', 'FALSE_POSITIVE', 'INCONCLUSIVE',
                            'ACCEPTED_RISK'
                        )),
    detection_method    TEXT NOT NULL,
    impact_statement    TEXT,
    remediation_guidance TEXT,
    is_included_in_report INTEGER NOT NULL DEFAULT 1 CHECK(is_included_in_report IN (0, 1)),
    first_detected_at   TEXT NOT NULL,
    last_verified_at    TEXT NOT NULL,
    FOREIGN KEY (investigation_id) REFERENCES investigations (investigation_id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES sessions (session_id) ON DELETE CASCADE,
    FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE SET NULL
);

CREATE UNIQUE INDEX uq_findings_dedup 
    ON findings (investigation_id, stable_finding_key);
CREATE INDEX idx_findings_inv_severity 
    ON findings (investigation_id, severity, verification_status);

-- ----------------------------------------------------------------------------
-- 14. Evidence Artifacts (Physical Filesystem References)
-- ----------------------------------------------------------------------------
CREATE TABLE evidence_artifacts (
    artifact_id         TEXT PRIMARY KEY NOT NULL CHECK(length(artifact_id) = 36),
    session_id          TEXT NOT NULL,
    finding_id          TEXT, -- NULL if general reconnaissance evidence
    relative_path       TEXT NOT NULL, -- e.g., 'evidence/nmap_scan_8080.xml'
    file_name           TEXT NOT NULL,
    file_type           TEXT NOT NULL CHECK(file_type IN ('RAW_OUTPUT', 'PCAP', 'SCREENSHOT', 'CONFIG_DUMP', 'CERTIFICATE', 'HTTP_TRACE')),
    mime_type           TEXT NOT NULL,
    file_size_bytes     INTEGER NOT NULL CHECK(file_size_bytes >= 0),
    sha256_hash         TEXT NOT NULL CHECK(length(sha256_hash) = 64),
    description         TEXT,
    captured_at         TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions (session_id) ON DELETE CASCADE,
    FOREIGN KEY (finding_id) REFERENCES findings (finding_id) ON DELETE SET NULL
);

CREATE INDEX idx_artifacts_session ON evidence_artifacts (session_id);
CREATE INDEX idx_artifacts_finding ON evidence_artifacts (finding_id);
CREATE INDEX idx_artifacts_hash ON evidence_artifacts (sha256_hash);

-- ----------------------------------------------------------------------------
-- 15. Reports (Markdown & Standalone HTML Deliverables)
-- ----------------------------------------------------------------------------
CREATE TABLE reports (
    report_id           TEXT PRIMARY KEY NOT NULL CHECK(length(report_id) = 36),
    investigation_id    TEXT NOT NULL,
    session_id          TEXT NOT NULL,
    report_version      INTEGER NOT NULL DEFAULT 1,
    report_type         TEXT NOT NULL CHECK(report_type IN ('INTERIM', 'FINAL')),
    markdown_path       TEXT NOT NULL, -- Relative path to report.md
    markdown_sha256     TEXT NOT NULL,
    html_path           TEXT NOT NULL, -- Relative path to report.html
    html_sha256         TEXT NOT NULL,
    findings_count_json TEXT NOT NULL, -- JSON blob of counts: {"HIGH": 2, "MED": 5}
    generated_at        TEXT NOT NULL,
    FOREIGN KEY (investigation_id) REFERENCES investigations (investigation_id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES sessions (session_id) ON DELETE CASCADE
);

CREATE INDEX idx_reports_investigation ON reports (investigation_id, report_version DESC);

-- ----------------------------------------------------------------------------
-- 16. Network Audit Logs (Egress Proxy Flow Records)
-- ----------------------------------------------------------------------------
CREATE TABLE network_audit_logs (
    log_id              TEXT PRIMARY KEY NOT NULL CHECK(length(log_id) = 36),
    session_id          TEXT NOT NULL,
    timestamp           TEXT NOT NULL,
    source_ip           TEXT NOT NULL,
    destination_host    TEXT NOT NULL,
    destination_ip      TEXT,
    destination_port    INTEGER NOT NULL,
    protocol            TEXT NOT NULL,
    decision            TEXT NOT NULL CHECK(decision IN ('ALLOWED', 'BLOCKED')),
    block_reason        TEXT,
    bytes_transferred   INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (session_id) REFERENCES sessions (session_id) ON DELETE CASCADE
);

CREATE INDEX idx_net_audit_session ON network_audit_logs (session_id, decision);

-- ----------------------------------------------------------------------------
-- 17. System Audit Log Entries (Security & Mutation Ledger)
-- ----------------------------------------------------------------------------
CREATE TABLE audit_log_entries (
    entry_id            TEXT PRIMARY KEY NOT NULL CHECK(length(entry_id) = 36),
    investigation_id    TEXT NOT NULL,
    session_id          TEXT,
    event_timestamp     TEXT NOT NULL,
    actor               TEXT NOT NULL CHECK(actor IN ('AGENT', 'USER', 'SYSTEM')),
    event_type          TEXT NOT NULL,
    event_payload_json  TEXT NOT NULL,
    FOREIGN KEY (investigation_id) REFERENCES investigations (investigation_id) ON DELETE CASCADE
);

CREATE INDEX idx_audit_log_inv_time ON audit_log_entries (investigation_id, event_timestamp DESC);
