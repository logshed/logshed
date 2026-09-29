"""
Pydantic v2 schemas and models for LogShed API.
"""

from datetime import datetime
import ipaddress
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.config import DEFAULT_AI_MODEL, get_max_retention_days



# ---------------------------------------------------------------------------
# Auth Models
# ---------------------------------------------------------------------------

class SetupRequest(BaseModel):
    """Payload for first-run admin account creation."""
    password: str = Field(..., min_length=8, max_length=128, description="Admin password (8-128 characters)")


class LoginRequest(BaseModel):
    """Payload for session login."""
    password: str = Field(..., max_length=128, description="Admin password")


class PasswordChangeRequest(BaseModel):
    """Payload for changing admin password."""
    current_password: str = Field(..., min_length=1, max_length=128, description="Current password")
    new_password: str = Field(..., min_length=8, max_length=128, description="New password (8-128 characters)")


class AuthStatusResponse(BaseModel):
    """Authentication and setup status."""
    setup_required: bool
    authenticated: bool


class MessageResponse(BaseModel):
    """Generic status response."""
    status: str = "ok"
    detail: Optional[str] = None


# ---------------------------------------------------------------------------
# Log Models
# ---------------------------------------------------------------------------

class LogEntry(BaseModel):
    """Single structured log record."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    timestamp: str
    received_at: str
    source_ip: str
    source_alias: str
    app_name: str
    facility: int
    severity: int
    message: str
    raw: str


class LogListResponse(BaseModel):
    """Paginated logs response."""
    logs: list[LogEntry]
    total: int
    limit: int
    offset: int


class LogContextResponse(BaseModel):
    """Surrounding context lines for a given log ID."""
    target_id: int
    logs: list[LogEntry]


class LogFacetsResponse(BaseModel):
    """Distinct sources, apps, and bidirectional mappings across the database."""
    sources: list[str]
    apps: list[str]
    host_to_apps: dict[str, list[str]]
    app_to_hosts: dict[str, list[str]]


# ---------------------------------------------------------------------------
# Settings Models
# ---------------------------------------------------------------------------

class SettingsResponse(BaseModel):
    """Application runtime configuration response with masked secrets."""
    ai_enabled: bool = False
    ai_provider: str = "gemini"
    ai_model: str = DEFAULT_AI_MODEL
    ai_fallback_models: str = ""
    ai_api_key: str = ""
    ai_base_url: Optional[str] = None
    ai_system_prompt: str = ""
    retention_days: int = 14
    max_retention_days: int = Field(default_factory=get_max_retention_days)
    retention_overridden: bool = False
    has_ai_api_key: bool = False
    internal_log_level: str = "WARNING"
    check_for_updates: bool = True
    maintenance_until: Optional[str] = None

    # Advanced System Settings
    ai_timeout: float = 45.0
    ai_thinking_budget: int = 1024
    app_url: str = ""
    allow_private_notification_targets: bool = True
    enable_docker: bool = True
    docker_exclude_containers: str = ""
    docker_source_alias: str = "docker"
    trusted_proxies: str = ""
    trust_docker_proxies: bool = False
    cookie_secure: bool = False
    syslog_max_tcp_connections: int = 250
    syslog_tcp_inactivity_timeout: float = 0.0

    # Daily Digest Settings
    daily_digest_enabled: bool = False
    daily_digest_channel_id: Optional[int] = None
    daily_digest_schedule_time: str = "09:00"
    daily_digest_last_run: Optional[str] = None

    @model_validator(mode="after")
    def clamp_retention_days(self) -> "SettingsResponse":
        if self.retention_overridden:
            self.retention_days = self.max_retention_days
        elif self.retention_days > self.max_retention_days:
            self.retention_days = self.max_retention_days
        elif self.retention_days < 1:
            self.retention_days = 1
        return self


class SettingsUpdateRequest(BaseModel):
    """Payload for updating runtime settings."""
    ai_enabled: Optional[bool] = None
    ai_provider: Optional[str] = None
    ai_model: Optional[str] = None
    ai_fallback_models: Optional[str] = None
    ai_api_key: Optional[str] = None
    ai_base_url: Optional[str] = None
    ai_system_prompt: Optional[str] = None
    retention_days: Optional[int] = Field(
        None,
        description=(
            "Log retention period in days (1 to MAX_RETENTION_DAYS, default 14)."
        ),
    )
    internal_log_level: Optional[str] = Field(
        None,
        description="Internal application log capture level (DEBUG, INFO, WARNING, ERROR, CRITICAL, DISABLED).",
    )
    check_for_updates: Optional[bool] = Field(
        None,
        description="Whether to check GHCR periodically for new stable releases.",
    )
    maintenance_until: Optional[str] = Field(
        None,
        description="Global alert maintenance window end time (ISO 8601 datetime string or null/empty to clear).",
    )

    # Advanced System Settings
    ai_timeout: Optional[float] = Field(
        None,
        description="AI completion request timeout in seconds (must be > 0.0).",
    )
    ai_thinking_budget: Optional[int] = Field(
        None,
        description="Thinking token budget for reasoning models (integer >= 0, 0 disables).",
    )
    app_url: Optional[str] = Field(
        None,
        description="Public instance URL used for notification incident links (HTTP/HTTPS URL or empty).",
    )
    allow_private_notification_targets: Optional[bool] = Field(
        None,
        description="Whether notification channels are permitted to target private LAN endpoints.",
    )
    enable_docker: Optional[bool] = Field(
        None,
        description="Enable container log tailing via Docker socket or proxy.",
    )
    docker_exclude_containers: Optional[str] = Field(
        None,
        description="Comma-separated container names or IDs to exclude from log ingestion.",
    )
    docker_source_alias: Optional[str] = Field(
        None,
        description="Source attribution alias assigned to logs ingested from Docker (1-64 characters).",
    )
    trusted_proxies: Optional[str] = Field(
        None,
        description="Comma-separated trusted reverse proxy IP addresses or CIDR ranges.",
    )
    trust_docker_proxies: Optional[bool] = Field(
        None,
        description="Whether to trust Docker bridge networks (172.16.0.0/12) as reverse proxies.",
    )
    cookie_secure: Optional[bool] = Field(
        None,
        description="Force the Secure attribute on HTTP session cookies.",
    )
    syslog_max_tcp_connections: Optional[int] = Field(
        None,
        description="Maximum concurrent Syslog TCP connections allowed (integer >= 1).",
    )
    syslog_tcp_inactivity_timeout: Optional[float] = Field(
        None,
        description="Syslog TCP client inactivity timeout in seconds (float >= 0.0, 0 disables).",
    )
    daily_digest_enabled: Optional[bool] = Field(
        None,
        description="Enable automated 24-hour daily digest notification.",
    )
    daily_digest_channel_id: Optional[int] = Field(
        None,
        description="Target notification channel ID for daily digest (null sends to all enabled channels).",
    )
    daily_digest_schedule_time: Optional[str] = Field(
        None,
        description="Local time of day (HH:MM) to dispatch the daily digest.",
    )

    @field_validator("daily_digest_schedule_time")
    @classmethod
    def validate_daily_digest_schedule_time(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v_clean = v.strip()
            parts = v_clean.split(":")
            if len(parts) != 2:
                raise ValueError("daily_digest_schedule_time must be in HH:MM format")
            try:
                hour = int(parts[0])
                minute = int(parts[1])
            except ValueError:
                raise ValueError("daily_digest_schedule_time must contain numeric hour and minute")
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError("daily_digest_schedule_time hour must be 00-23 and minute 00-59")
            return f"{hour:02d}:{minute:02d}"
        return v

    @field_validator("ai_base_url")
    @classmethod
    def validate_ai_base_url(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v_clean = v.strip()
            if not v_clean:
                return ""
            from urllib.parse import urlparse
            parsed = urlparse(v_clean)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise ValueError("ai_base_url must be a valid HTTP or HTTPS URL")
            return v_clean
        return v

    @field_validator("retention_days")
    @classmethod
    def validate_retention_days(cls, v: Optional[int]) -> Optional[int]:
        if v is not None:
            max_days = get_max_retention_days()
            if v < 1 or v > max_days:
                raise ValueError(f"Retention days must be between 1 and {max_days}")
        return v

    @field_validator("internal_log_level")
    @classmethod
    def validate_internal_log_level(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v_clean = v.strip().upper()
            from app.core.config import VALID_LOG_LEVELS, to_canonical_log_level_name
            if v_clean not in VALID_LOG_LEVELS:
                valid_keys = ", ".join(sorted(VALID_LOG_LEVELS.keys()))
                raise ValueError(f"Invalid internal_log_level '{v}'. Must be one of: {valid_keys}")
            return to_canonical_log_level_name(v_clean)
        return v

    @field_validator("ai_timeout")
    @classmethod
    def validate_ai_timeout(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v <= 0.0:
            raise ValueError("ai_timeout must be a float greater than 0.0")
        return v

    @field_validator("ai_thinking_budget")
    @classmethod
    def validate_ai_thinking_budget(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 0:
            raise ValueError("ai_thinking_budget must be an integer greater than or equal to 0")
        return v

    @field_validator("app_url")
    @classmethod
    def validate_app_url(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            clean = v.strip()
            if not clean:
                return ""
            from urllib.parse import urlparse
            parsed = urlparse(clean)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise ValueError("app_url must be a valid HTTP or HTTPS URL or empty")
            return clean.rstrip("/")
        return v

    @field_validator("docker_source_alias")
    @classmethod
    def validate_docker_source_alias(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            clean = v.strip()
            if not (1 <= len(clean) <= 64):
                raise ValueError("docker_source_alias must be between 1 and 64 characters")
            return clean
        return v

    @field_validator("trusted_proxies")
    @classmethod
    def validate_trusted_proxies(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            clean = v.strip()
            if not clean:
                return ""
            for part in clean.split(","):
                part_clean = part.strip()
                if not part_clean:
                    continue
                if part_clean.startswith("[") and "]" in part_clean:
                    bracket_end = part_clean.find("]")
                    prefix = part_clean[1:bracket_end]
                    remainder = part_clean[bracket_end + 1:]
                    part_clean = prefix + remainder
                try:
                    ipaddress.ip_network(part_clean, strict=False)
                except ValueError:
                    raise ValueError(f"Invalid IP address or CIDR range in trusted_proxies: '{part.strip()}'")
            return clean
        return v

    @field_validator("syslog_max_tcp_connections")
    @classmethod
    def validate_syslog_max_tcp_connections(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 1:
            raise ValueError("syslog_max_tcp_connections must be an integer greater than or equal to 1")
        return v

    @field_validator("syslog_tcp_inactivity_timeout")
    @classmethod
    def validate_syslog_tcp_inactivity_timeout(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v < 0.0:
            raise ValueError("syslog_tcp_inactivity_timeout must be a float greater than or equal to 0.0")
        return v


SettingsUpdate = SettingsUpdateRequest



# ---------------------------------------------------------------------------
# Host Aliases Models
# ---------------------------------------------------------------------------

class HostAliasCreate(BaseModel):
    """Payload for creating or updating a host alias."""
    ip: str = Field(..., min_length=1, description="IP address or subnet key")
    alias: str = Field(..., min_length=1, description="Human-readable host name")
    notes: Optional[str] = None

    @field_validator("ip")
    @classmethod
    def validate_ip(cls, v: str) -> str:
        clean = v.strip()
        if clean.lower() == "docker":
            return clean.lower()
        try:
            ipaddress.ip_address(clean)
        except ValueError:
            raise ValueError("Invalid IP address format. Expected valid IPv4 or IPv6 address.")
        return clean


class HostAliasResponse(BaseModel):
    """Host alias mapping details."""
    ip: str
    alias: str
    notes: Optional[str] = None
    created_at: str


# ---------------------------------------------------------------------------
# System, Health & Storage Models
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    """Container and ingestion health status."""
    status: str
    db: Optional[str] = None
    queue_depth: Optional[int] = None
    dropped_logs: Optional[int] = None
    ingest_rate: Optional[float] = None


class StorageMetricItem(BaseModel):
    """Storage metrics snapshot."""
    recorded_at: str
    db_size_bytes: int
    disk_free_bytes: int
    disk_total_bytes: int
    total_logs_count: int


class StorageOverviewResponse(BaseModel):
    """Storage overview with current metrics and history."""
    db_size_bytes: int
    disk_free_bytes: int
    disk_total_bytes: int
    total_logs_count: int
    history: list[StorageMetricItem]


class PruneResponse(BaseModel):
    """Response returned after manual retention prune."""
    status: str = "ok"
    deleted_logs: int
    deleted_metrics: int
    metrics: StorageMetricItem


class VacuumResponse(BaseModel):
    """Response returned after database vacuum compaction."""
    status: str = "ok"
    previous_size_bytes: int
    new_size_bytes: int
    reclaimed_bytes: int
    metrics: StorageMetricItem


class VersionResponse(BaseModel):
    """Application version and GHCR update availability."""
    current_version: str
    latest_version: Optional[str] = None
    update_available: bool = False
    check_enabled: bool = True
    checked_at: Optional[float] = None



# ---------------------------------------------------------------------------
# AI Models
# ---------------------------------------------------------------------------

class AiPreviewRequest(BaseModel):
    """Payload for generating redacted AI prompt preview."""
    log_ids: list[int] = Field(..., min_length=1, max_length=200)
    user_context: Optional[str] = None
    prompt_override: Optional[str] = None


class AiPreviewResponse(BaseModel):
    """Preview of redacted prompt and token estimate."""
    redacted_prompt: str
    estimated_tokens: int
    provider: str
    model: str
    fallback_models: list[str] = Field(default_factory=list)
    log_count: int
    source_alias: str
    app_name: str
    system_prompt: str = ""
    ai_enabled: bool = True
    has_ai_api_key: bool = True


class AiDiagnosisRequest(BaseModel):
    """Payload for triggering on-demand AI diagnosis."""
    log_ids: list[int] = Field(..., min_length=1, max_length=200)
    user_context: Optional[str] = None
    prompt_override: Optional[str] = None
    system_prompt_override: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    fallback_models: Optional[list[str]] = None


class AiDiagnosisResponse(BaseModel):
    """Structured root-cause diagnosis returned from LLM."""
    summary: str
    root_cause: str
    remediation: str
    model_used: str
    fallback_used: bool = False
    fallback_attempts: list[str] = Field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_thoughts: int = 0
    tokens_used: int
    audit_id: Optional[int] = None


class AiAuditItem(BaseModel):
    """Single historical AI analysis record."""
    id: int
    timestamp: str
    source_alias: str
    app_name: str
    log_count: int
    user_context: Optional[str] = None
    model: str
    prompt_sent: str
    response_text: str
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_thoughts: int = 0
    tokens_used: int
    system_prompt: Optional[str] = None
    trigger_source: str = Field("on-demand", description="Trigger origin: 'on-demand' or 'alert'")


AiAuditEntry = AiAuditItem


class AiAuditListResponse(BaseModel):
    """Paginated list of historical AI analysis audits."""
    items: list[AiAuditItem]
    total: int


class AiAuditDeleteResponse(BaseModel):
    """Status response for deleting AI audit records."""
    status: str = "ok"
    deleted_id: Optional[int] = None
    deleted_count: Optional[int] = None


class AiModelInfo(BaseModel):
    """Information regarding an available model discovered from a provider."""
    id: str
    name: str
    description: Optional[str] = None
    supports_thinking: bool = False
    is_deprecated: bool = False


class AiModelsResponse(BaseModel):
    """Response payload containing available models for a provider."""
    provider: str
    models: list[AiModelInfo] = Field(default_factory=list)
    has_api_key: bool = True
    cached_at: Optional[str] = None
    is_live: bool = True
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Drop Rules Models
# ---------------------------------------------------------------------------

class DropRuleCreate(BaseModel):
    """Payload for creating an ingestion drop rule."""
    name: Optional[str] = Field(None, max_length=100, description="Friendly drop rule name")
    source_pattern: Optional[str] = Field(None, max_length=255, description="Host IP or alias pattern (supports wildcards)")
    app_pattern: Optional[str] = Field(None, max_length=255, description="Container or application pattern (supports wildcards)")
    message_pattern: Optional[str] = Field("*", max_length=1000, description="Substring, wildcard, or regular expression match pattern")
    is_regex: bool = Field(False, description="Whether message_pattern should be evaluated as a regular expression")
    is_enabled: bool = Field(True, description="Whether the rule is active")
    severity_threshold: Optional[int] = Field(None, ge=0, le=7, description="Drop logs at or below this severity level (0-7, null matches any)")


class DropRuleUpdate(BaseModel):
    """Payload for updating an ingestion drop rule."""
    name: Optional[str] = Field(None, max_length=100, description="Friendly drop rule name")
    source_pattern: Optional[str] = Field(None, max_length=255)
    app_pattern: Optional[str] = Field(None, max_length=255)
    message_pattern: Optional[str] = Field(None, max_length=1000)
    is_regex: Optional[bool] = None
    is_enabled: Optional[bool] = None
    severity_threshold: Optional[int] = Field(None, ge=0, le=7)
    reset_counter: Optional[bool] = None


class DropRuleResponse(BaseModel):
    """Drop rule response representation."""
    id: int
    name: Optional[str] = None
    source_pattern: Optional[str] = None
    app_pattern: Optional[str] = None
    message_pattern: str
    is_regex: bool = False
    is_enabled: bool = True
    severity_threshold: Optional[int] = None
    dropped_count: int = 0
    created_at: str


class DropRuleTestRequest(BaseModel):
    """Payload for testing candidate drop rule patterns against sample log data."""
    source_pattern: Optional[str] = Field(None, max_length=255)
    app_pattern: Optional[str] = Field(None, max_length=255)
    message_pattern: Optional[str] = Field("*", max_length=1000)
    is_regex: bool = Field(False)
    severity_threshold: Optional[int] = Field(None, ge=0, le=7)
    sample_message: str = Field(..., description="Sample message payload to test against")
    sample_source: Optional[str] = Field(None, description="Sample host alias or IP")
    sample_app: Optional[str] = Field(None, description="Sample application or container name")
    sample_severity: Optional[int] = Field(None, ge=0, le=7)


class DropRuleTestResponse(BaseModel):
    """Result of testing a drop rule pattern."""
    matched: bool
    error: Optional[str] = None


class DropPresetResponse(BaseModel):
    """Predefined log drop rule preset."""
    id: str
    name: str
    description: str
    source_pattern: Optional[str] = None
    app_pattern: Optional[str] = None
    message_pattern: str
    is_regex: bool = False
    severity_threshold: Optional[int] = None
    is_custom: bool = False


class DropRuleExportItem(BaseModel):
    """Exportable drop rule definition."""
    model_config = ConfigDict(extra="ignore")
    name: Optional[str] = None
    source_pattern: Optional[str] = None
    app_pattern: Optional[str] = None
    message_pattern: str = "*"
    is_regex: bool = False
    is_enabled: bool = True
    severity_threshold: Optional[int] = None


class DropRuleExportBundle(BaseModel):
    """Bundle containing multiple exported drop rules."""
    version: str = "1"
    exported_at: str
    drop_rules: list[DropRuleExportItem]


class DropRuleExportSingle(BaseModel):
    """Export container for a single drop rule."""
    version: str = "1"
    exported_at: str
    drop_rule: DropRuleExportItem


class DropRuleImportResponse(BaseModel):
    """Outcome summary for imported drop rules."""
    imported: int
    skipped: int
    errors: list[str] = Field(default_factory=list)



# ---------------------------------------------------------------------------
# Saved Views Models
# ---------------------------------------------------------------------------

class SavedViewCreate(BaseModel):
    """Payload for creating a saved search/filter view."""
    name: str = Field(..., min_length=1, max_length=100, description="Human-readable view name")
    query_params: dict[str, Any] = Field(..., description="JSON search and filter parameter dictionary")
    is_pinned: bool = Field(False, description="Whether the view is pinned as a quick chip")


class SavedViewUpdate(BaseModel):
    """Payload for modifying a saved view."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    query_params: Optional[dict[str, Any]] = None
    is_pinned: Optional[bool] = None


class SavedViewResponse(BaseModel):
    """Saved view representation."""
    id: int
    name: str
    query_params: dict[str, Any]
    is_pinned: bool = False
    created_at: str


# ---------------------------------------------------------------------------
# Notification Channel Models
# ---------------------------------------------------------------------------

class NotificationChannelCreate(BaseModel):
    """Payload for creating a new notification channel."""
    name: str = Field(..., min_length=1, max_length=100, description="Friendly channel name")
    url: str = Field(..., min_length=5, max_length=2000, description="Apprise-compatible notification target URL")
    is_enabled: bool = Field(True, description="Whether the channel is active")


class NotificationChannelUpdate(BaseModel):
    """Payload for updating an existing notification channel."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    url: Optional[str] = Field(None, min_length=5, max_length=2000)
    is_enabled: Optional[bool] = None


class NotificationChannelResponse(BaseModel):
    """Masked representation of a notification channel."""
    id: int
    name: str
    url: str
    is_enabled: bool
    created_at: str
    updated_at: str


class NotificationTestRequest(BaseModel):
    """Payload for testing an existing channel or a raw URL before saving."""
    channel_id: Optional[int] = Field(None, description="Existing channel ID to test")
    url: Optional[str] = Field(None, description="Raw candidate URL to test before creating")


class NotificationTestResponse(BaseModel):
    """Result of testing a notification target."""
    success: bool
    message: str


class DailyDigestRunResponse(BaseModel):
    """Result of running or testing the 24-hour daily digest."""
    status: str = "ok"
    history_id: Optional[int] = None
    total_logs: int = 0
    error_count: int = 0
    top_errors: list[dict[str, Any]] = Field(default_factory=list)
    top_services: list[dict[str, Any]] = Field(default_factory=list)
    storage_delta: str = "0 B"
    channel_id: Optional[int] = None
    notification_sent: bool = False
    triggered_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Alert Rules, Presets & History Models
# ---------------------------------------------------------------------------

class AlertRuleCreate(BaseModel):
    """Payload for creating a new alert rule."""
    name: str = Field(..., min_length=1, max_length=100, description="Friendly alert rule name")
    rule_type: str = Field("threshold", description="Rule type: threshold, pattern, or rate")
    channel_id: Optional[int] = Field(None, description="Target notification channel ID (null dispatches to all enabled channels)")
    filter_app: Optional[str] = Field(None, max_length=100, description="Optional application or container filter")
    filter_severity: Optional[int] = Field(None, ge=0, le=7, description="Maximum severity threshold (0-7, lower is more critical)")
    match_pattern: Optional[str] = Field(None, max_length=1000, description="Regex or keyword pattern to match against log messages")
    threshold_count: int = Field(1, ge=1, le=100000, description="Occurrences or logs/sec needed within window to trigger")
    window_seconds: int = Field(60, ge=1, le=86400, description="Sliding window duration in seconds")
    cooldown_seconds: int = Field(300, ge=5, le=86400, description="Cooldown dampening duration in seconds")
    ai_enrichment: bool = Field(False, description="Whether to enrich incident alerts with LLM root-cause analysis")
    is_enabled: bool = Field(True, description="Whether the rule is actively evaluated")


class AlertRuleUpdate(BaseModel):
    """Payload for updating an existing alert rule."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    rule_type: Optional[str] = Field(None, description="Rule type: threshold, pattern, or rate")
    channel_id: Optional[int] = None
    filter_app: Optional[str] = Field(None, max_length=100)
    filter_severity: Optional[int] = Field(None, ge=0, le=7)
    match_pattern: Optional[str] = Field(None, max_length=1000)
    threshold_count: Optional[int] = Field(None, ge=1, le=100000)
    window_seconds: Optional[int] = Field(None, ge=1, le=86400)
    cooldown_seconds: Optional[int] = Field(None, ge=5, le=86400)
    ai_enrichment: Optional[bool] = None
    is_enabled: Optional[bool] = None
    reset_cooldown: Optional[bool] = Field(False, description="Clear active cooldown suppression immediately")


class AlertRuleResponse(BaseModel):
    """Representation of an alert rule."""
    id: int
    name: str
    rule_type: str
    channel_id: Optional[int] = None
    filter_app: Optional[str] = None
    filter_severity: Optional[int] = None
    match_pattern: Optional[str] = None
    threshold_count: int = 1
    window_seconds: int = 60
    cooldown_seconds: int = 300
    ai_enrichment: bool = False
    is_enabled: bool = True
    trigger_count: int = 0
    last_triggered_at: Optional[str] = None
    suppress_until: Optional[str] = None
    created_at: str


class AlertTestRequest(BaseModel):
    """Payload for testing an alert rule pattern against sample input."""
    rule_type: str = "threshold"
    filter_app: Optional[str] = None
    filter_severity: Optional[int] = None
    match_pattern: Optional[str] = None
    sample_message: str = Field(..., description="Sample message text to test")
    sample_app: Optional[str] = Field(None, description="Sample app name")
    sample_severity: Optional[int] = Field(6, description="Sample severity (0-7)")


class AlertTestResponse(BaseModel):
    """Result of testing an alert rule pattern."""
    matched: bool
    extracted_ip: Optional[str] = None
    error: Optional[str] = None


class AlertPresetResponse(BaseModel):
    """Predefined alert preset."""
    id: str
    name: str
    description: str
    rule_type: str
    filter_app: Optional[str] = None
    filter_severity: Optional[int] = None
    match_pattern: Optional[str] = None
    threshold_count: int = 1
    window_seconds: int = 60
    cooldown_seconds: int = 300
    ai_enrichment: bool = True
    is_custom: bool = False


# Backward compatibility alias
SecurityPresetResponse = AlertPresetResponse


class AlertRuleExportItem(BaseModel):
    """Exportable alert rule definition."""
    model_config = ConfigDict(extra="ignore")
    name: str
    rule_type: str = "threshold"
    filter_app: Optional[str] = None
    filter_severity: Optional[int] = None
    match_pattern: Optional[str] = None
    threshold_count: int = 1
    window_seconds: int = 60
    cooldown_seconds: int = 300
    ai_enrichment: bool = False
    is_enabled: bool = True


class AlertRuleExportBundle(BaseModel):
    """Bundle containing multiple exported alert rules."""
    version: str = "1"
    exported_at: str
    alert_rules: list[AlertRuleExportItem]


class AlertRuleExportSingle(BaseModel):
    """Export container for a single alert rule."""
    version: str = "1"
    exported_at: str
    alert_rule: AlertRuleExportItem


class AlertRuleImportResponse(BaseModel):
    """Outcome summary for imported alert rules."""
    imported: int
    skipped: int
    errors: list[str] = Field(default_factory=list)


class AlertPresetInstallRequest(BaseModel):
    """Payload for installing a security preset."""
    channel_id: Optional[int] = Field(None, description="Target notification channel ID")


class AlertHistoryItem(BaseModel):
    """Alert firing or analysis event record."""
    id: int
    rule_id: Optional[int] = None
    rule_name: str
    channel_id: Optional[int] = None
    trigger_count: int = 1
    sample_log: Optional[str] = None
    incident_summary: Optional[str] = None
    ai_enrichment: bool = False
    ai_model: Optional[str] = None
    ai_audit_id: Optional[int] = None
    triggered_at: str
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    tokens_thoughts: Optional[int] = None
    tokens_used: Optional[int] = None
    prompt_sent: Optional[str] = None
    system_prompt: Optional[str] = None
    response_text: Optional[str] = None
    source_alias: Optional[str] = None
    app_name: Optional[str] = None
    user_context: Optional[str] = None


class AlertHistoryListResponse(BaseModel):
    """Paginated list of historical alert firing events."""
    items: list[AlertHistoryItem]
    total: int
    limit: int
    offset: int


class MaintenanceWindowRequest(BaseModel):
    """Payload for setting or clearing the on-demand alert maintenance window."""
    until: Optional[str] = Field(None, description="End time in ISO 8601 format, or null to clear.")


class MaintenanceSchedule(BaseModel):
    """Configuration for a recurring alert maintenance window schedule."""
    id: str = Field(default_factory=lambda: "")
    name: str = Field(..., min_length=1, max_length=100, description="Schedule name")
    enabled: bool = Field(True, description="Whether the schedule is enabled")
    recurrence: str = Field("weekly", description="daily, weekly, or monthly")
    start_time: str = Field("02:00", description="Start time in 24h HH:MM format")
    duration_minutes: int = Field(60, ge=1, le=1440, description="Duration in minutes (1 to 1440)")
    day_of_week: Optional[int] = Field(0, ge=0, le=6, description="0=Sunday, 1=Monday, ..., 6=Saturday")
    day_of_month: Optional[int] = Field(1, ge=1, le=31, description="Day of month 1 to 31")
    is_active: Optional[bool] = Field(False, description="Whether the schedule is active right now")
    next_run: Optional[str] = Field(None, description="ISO timestamp of next scheduled window start")


class MaintenanceSchedulesUpdateRequest(BaseModel):
    """Payload for saving configured maintenance schedules."""
    schedules: list[MaintenanceSchedule]


class MaintenanceWindowResponse(BaseModel):
    """Current status of the global alert maintenance window."""
    active: bool
    until: Optional[str] = None
    reason: Optional[str] = None
    schedule_name: Optional[str] = None
    on_demand_until: Optional[str] = None
    schedules: list[MaintenanceSchedule] = Field(default_factory=list)
    server_time: Optional[str] = None
    server_timezone: Optional[str] = None



