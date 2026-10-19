"""
Application configuration for LogShed.
Manages environment variables, filesystem paths, and defaults.
"""

import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional, Union

logger = logging.getLogger(__name__)

def get_data_dir() -> Path:
    """Returns the configured data directory path."""
    return Path(os.environ.get("DATA_DIR", "/data"))

def get_db_path() -> Path:
    """Returns the configured database file path."""
    env_path = os.environ.get("DB_PATH")
    if env_path:
        return Path(env_path)
    return get_data_dir() / "logs.db"

def get_secret_key_path() -> Path:
    """Returns the path to the persisted secret key file."""
    env_path = os.environ.get("SECRET_KEY_PATH")
    if env_path:
        return Path(env_path)
    return get_data_dir() / ".secret_key"

def get_secret_key_override() -> Optional[str]:
    """Returns the secret key override from environment if provided."""
    return os.environ.get("LOGSHED_SECRET_KEY")

def get_port() -> int:
    """Returns the web server listening port."""
    try:
        return int(os.environ.get("PORT", "8080"))
    except ValueError:
        return 8080

def get_syslog_port() -> int:
    """
    Returns the configured Syslog UDP and TCP listening port.
    Reads SYSLOG_PORT environment variable (default: 1514).
    Validates that the port is an integer in the range 1 <= port <= 65535,
    falling back to 1514 if unset or invalid.
    """
    raw = os.environ.get("SYSLOG_PORT", "1514")
    try:
        val = int(raw.strip())
        if 1 <= val <= 65535:
            return val
    except (ValueError, TypeError):
        pass
    return 1514

def get_syslog_max_tcp_connections() -> int:
    """
    Returns the maximum concurrent Syslog TCP connections allowed.
    Resolves using three-tier hierarchy: system_settings -> SYSLOG_MAX_TCP_CONNECTIONS -> 250.
    """
    return int(get_cached_setting("syslog_max_tcp_connections", 250))

def get_syslog_max_tcp_connections_per_ip() -> int:
    """
    Returns the maximum concurrent Syslog TCP connections allowed from one source IP.
    Reads SYSLOG_MAX_TCP_CONNECTIONS_PER_IP (default: 20); values that are not
    integers >= 1 fall back to 20.
    """
    raw = os.environ.get("SYSLOG_MAX_TCP_CONNECTIONS_PER_IP", "20")
    try:
        val = int(raw.strip())
        if val >= 1:
            return val
    except (ValueError, TypeError):
        pass
    return 20

DEFAULT_SYSLOG_UDP_RECEIVE_BUFFER_BYTES = 8 * 1024 * 1024
_MAX_SOCKET_BUFFER_BYTES = 2**31 - 1

def get_syslog_udp_receive_buffer_bytes() -> int:
    """
    Returns the receive buffer size (SO_RCVBUF) requested for the Syslog UDP socket, in bytes.
    Reads SYSLOG_UDP_RECEIVE_BUFFER_BYTES (default: 8 MiB); 0 leaves the OS default. A value
    that is not an integer from 0 to 2147483647 falls back to the default with a warning.
    """
    raw = os.environ.get("SYSLOG_UDP_RECEIVE_BUFFER_BYTES", "").strip()
    if not raw:
        return DEFAULT_SYSLOG_UDP_RECEIVE_BUFFER_BYTES
    try:
        val = int(raw)
        if 0 <= val <= _MAX_SOCKET_BUFFER_BYTES:
            return val
    except ValueError:
        pass
    logging.getLogger(__name__).warning(
        f"Invalid SYSLOG_UDP_RECEIVE_BUFFER_BYTES value {raw!r}: expected a whole number of bytes "
        f"from 0 to {_MAX_SOCKET_BUFFER_BYTES}. Using the default of {DEFAULT_SYSLOG_UDP_RECEIVE_BUFFER_BYTES}."
    )
    return DEFAULT_SYSLOG_UDP_RECEIVE_BUFFER_BYTES

def get_syslog_tcp_inactivity_timeout() -> float:
    """
    Returns the Syslog TCP inactivity timeout in seconds.
    Resolves using three-tier hierarchy: system_settings -> SYSLOG_TCP_INACTIVITY_TIMEOUT -> 0.0.
    """
    return float(get_cached_setting("syslog_tcp_inactivity_timeout", 0.0))

def get_docker_host() -> str:
    """Returns the Docker host socket or proxy address."""
    return os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock")

def get_docker_additional_hosts() -> list[dict[str, Any]]:
    """
    Parse and validate DOCKER_ADDITIONAL_HOSTS from the environment.
    Returns a list of validated host dictionaries.
    """
    raw = os.environ.get("DOCKER_ADDITIONAL_HOSTS", "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except Exception as e:
        logger.warning(f"Invalid JSON in DOCKER_ADDITIONAL_HOSTS: {e}")
        return []
    if not isinstance(parsed, list):
        logger.warning("DOCKER_ADDITIONAL_HOSTS must be a JSON array of objects.")
        return []

    results: list[dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        url = str(item.get("url", "")).strip()
        if not name or not url:
            continue
        results.append({
            "name": name,
            "url": url,
            "source_alias": str(item.get("source_alias", "docker")).strip() or "docker",
            "enabled": 0 if str(item.get("enabled", "1")).lower() in ("0", "false", "no", "disabled") else 1,
            "tls_enabled": 1 if bool(item.get("tls_enabled")) else 0,
            "ca_cert": str(item.get("ca_cert", "")).strip() or None if item.get("ca_cert") else None,
            "client_cert": str(item.get("client_cert", "")).strip() or None if item.get("client_cert") else None,
            "client_key": str(item.get("client_key", "")).strip() or None if item.get("client_key") else None,
            "exclude_containers": str(item.get("exclude_containers", "")).strip(),
        })
    return results

def is_debug_or_dev() -> bool:
    """Returns True if running in development mode or debug is enabled."""
    return (
        os.environ.get("ENVIRONMENT", "").lower() == "development"
        or os.environ.get("DEBUG", "").lower() in ("true", "1", "yes")
    )


def get_cors_origins() -> list[str]:
    """
    Returns the list of allowed CORS origins.
    Strictly defaults to [] in production (same-origin React SPA bundle).
    Populates development origins only if ENVIRONMENT=development (DEBUG alone does not),
    or if explicitly overridden via the CORS_ORIGINS environment variable.
    A wildcard "*" entry is dropped: with credentials allowed, Starlette would echo any
    Origin back, letting every website send credentialed requests to LogShed.
    """
    env_origins = os.environ.get("CORS_ORIGINS")
    if env_origins is not None:
        origins = [origin.strip() for origin in env_origins.split(",") if origin.strip()]
        if "*" in origins:
            logging.getLogger(__name__).error(
                "CORS_ORIGINS contains '*', which is not allowed because LogShed sends credentialed "
                "cross-origin requests. The '*' entry is ignored; list each trusted origin instead."
            )
            origins = [origin for origin in origins if origin != "*"]
        return origins

    if os.environ.get("ENVIRONMENT", "").strip().lower() == "development":
        return [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:8080",
            "http://127.0.0.1:8080",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ]
    return []


def get_max_retention_days() -> int:
    """
    Returns the maximum allowed log retention period in days.
    Defaults to 30 days. Configurable via the MAX_RETENTION_DAYS environment variable.
    Enforced to have a minimum value of at least 1 day.
    """
    raw = os.environ.get("MAX_RETENTION_DAYS")
    if raw is not None:
        try:
            val = int(raw.strip())
            return max(1, val)
        except ValueError:
            pass
    return 30


def is_max_retention_days_overridden() -> bool:
    """
    Returns True if MAX_RETENTION_DAYS is explicitly defined as a valid integer in the environment.
    """
    raw = os.environ.get("MAX_RETENTION_DAYS")
    if raw is not None and raw.strip():
        try:
            int(raw.strip())
            return True
        except ValueError:
            return False
    return False


VALID_LOG_LEVELS: dict[str, Optional[int]] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
    "FATAL": logging.CRITICAL,
    "DISABLED": None,
    "OFF": None,
    "NONE": None,
    "FALSE": None,
    "0": None,
}

DEFAULT_AI_MODEL = "gemini-3.7-flash"
DEFAULT_AI_TIMEOUT: float = float(os.environ.get("LOGSHED_AI_TIMEOUT", "45.0"))
DEFAULT_AI_THINKING_BUDGET: int = int(os.environ.get("LOGSHED_AI_THINKING_BUDGET", "1024"))
DEFAULT_INTERNAL_LOG_LEVEL = logging.WARNING


def parse_internal_log_level(val: Optional[Union[str, int]]) -> Optional[int]:
    """
    Parses a log level representation into an integer logging level or None if disabled.
    Accepts string level names (DEBUG, INFO, WARNING, WARN, ERROR, CRITICAL, FATAL, DISABLED, OFF, NONE)
    or integer levels.
    Defaults to logging.WARNING if val is None or empty string.
    Falls back to logging.WARNING if val is unrecognized.
    """
    if val is None:
        return DEFAULT_INTERNAL_LOG_LEVEL
    if isinstance(val, int):
        return val
    s = str(val).strip().upper()
    if not s:
        return DEFAULT_INTERNAL_LOG_LEVEL
    if s in VALID_LOG_LEVELS:
        return VALID_LOG_LEVELS[s]
    try:
        return int(s)
    except ValueError:
        pass
    return DEFAULT_INTERNAL_LOG_LEVEL


def get_internal_log_level() -> Optional[int]:
    """
    Returns the configured logging level for LogShed's internal log handler.
    Reads the LOGSHED_INTERNAL_LOG_LEVEL environment variable (default: WARNING).
    Returns None if internal logging is disabled (e.g. 'DISABLED', 'OFF', 'NONE').
    """
    raw = os.environ.get("LOGSHED_INTERNAL_LOG_LEVEL")
    return parse_internal_log_level(raw)


def get_internal_log_level_name(level: Optional[int] = ...) -> str:
    """
    Returns the canonical string representation of an internal logging level.
    If level is omitted, reads from current environment configuration.
    """
    if level is ...:
        level = get_internal_log_level()
    if level is None:
        return "DISABLED"
    name = logging.getLevelName(level)
    if isinstance(name, str) and not name.startswith("Level "):
        return name
    return str(level)


def to_canonical_log_level_name(val: Optional[Union[str, int]]) -> str:
    """
    Converts any log level representation (string name, alias, integer, None)
    to its canonical string name: DEBUG, INFO, WARNING, ERROR, CRITICAL, or DISABLED.
    """
    parsed = parse_internal_log_level(val)
    return get_internal_log_level_name(parsed)


def get_all_system_settings(conn: sqlite3.Connection) -> dict[str, str]:
    """
    Select all system_settings from the database and decrypt encrypted values.
    Returns a dictionary of setting keys to decrypted string values.
    """
    from app.core.security import decrypt_value

    cursor = conn.cursor()
    cursor.execute("SELECT key, value, is_encrypted FROM system_settings")
    rows = cursor.fetchall()
    settings: dict[str, str] = {}
    for r in rows:
        if isinstance(r, sqlite3.Row):
            k, v, enc = r["key"], r["value"], bool(r["is_encrypted"])
        else:
            k, v, enc = r[0], r[1], bool(r[2])
        if enc and v:
            try:
                settings[k] = decrypt_value(v)
            except Exception:
                settings[k] = ""
        else:
            settings[k] = v or ""
    return settings


def _cast_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "1", "yes", "on")


def resolve_setting(
    db_settings: dict[str, str],
    key: str,
    env_var: Optional[str] = None,
    default: Any = None,
    caster: Callable[[str], Any] = str,
    env_aliases: Optional[list[str]] = None,
) -> Any:
    """
    Resolves configuration using the three-tier hierarchy:
    Tier 1: SQLite system_settings table (Web UI configuration)
    Tier 2: Primary environment variable or legacy aliases
    Tier 3: Built-in default constant
    """
    # Tier 1: Check database configuration
    if key in db_settings and db_settings[key] is not None and db_settings[key] != "":
        try:
            return caster(db_settings[key])
        except (ValueError, TypeError):
            pass

    # Tier 2: Check primary environment variable
    if env_var:
        env_val = os.environ.get(env_var)
        if env_val is not None and env_val.strip() != "":
            try:
                return caster(env_val.strip())
            except (ValueError, TypeError):
                pass

    # Tier 2b: Silent legacy aliases (undocumented, backward compatibility)
    if env_aliases:
        for alias in env_aliases:
            alias_val = os.environ.get(alias)
            if alias_val is not None and alias_val.strip() != "":
                try:
                    return caster(alias_val.strip())
                except (ValueError, TypeError):
                    pass

    # Tier 3: Hardcoded default
    return default


def _cast_ai_timeout(v: str) -> float:
    val = float(v)
    if val > 0.0:
        return val
    raise ValueError("ai_timeout must be > 0.0")


def _cast_ai_thinking_budget(v: str) -> int:
    val = int(v)
    if val >= 0:
        return val
    raise ValueError("ai_thinking_budget must be >= 0")


def _cast_syslog_max_connections(v: str) -> int:
    val = int(v)
    if val >= 1:
        return val
    raise ValueError("syslog_max_tcp_connections must be >= 1")


def _cast_syslog_inactivity_timeout(v: str) -> float:
    val = float(v)
    if val >= 0.0:
        return val
    raise ValueError("syslog_tcp_inactivity_timeout must be >= 0.0")


def resolve_all_system_settings(db_settings: dict[str, str]) -> dict[str, Any]:
    """
    Resolve all 12 runtime advanced system settings using the three-tier hierarchy.
    """
    return {
        "ai_timeout": resolve_setting(
            db_settings,
            key="ai_timeout",
            env_var="LOGSHED_AI_TIMEOUT",
            default=45.0,
            caster=_cast_ai_timeout,
        ),
        "ai_thinking_budget": resolve_setting(
            db_settings,
            key="ai_thinking_budget",
            env_var="LOGSHED_AI_THINKING_BUDGET",
            default=1024,
            caster=_cast_ai_thinking_budget,
        ),
        # Legacy Alias: app_url (lowercase) was supported alongside APP_URL.
        # Retained silently for backward compatibility with existing configurations.
        "app_url": resolve_setting(
            db_settings,
            key="app_url",
            env_var="APP_URL",
            env_aliases=["app_url"],
            default="",
            caster=lambda v: v.strip().rstrip("/"),
        ),
        "allow_private_notification_targets": resolve_setting(
            db_settings,
            key="allow_private_notification_targets",
            env_var="ALLOW_PRIVATE_NOTIFICATION_TARGETS",
            default=True,
            caster=_cast_bool,
        ),
        "enable_docker": resolve_setting(
            db_settings,
            key="enable_docker",
            env_var="ENABLE_DOCKER",
            default=True,
            caster=_cast_bool,
        ),
        "docker_exclude_containers": resolve_setting(
            db_settings,
            key="docker_exclude_containers",
            env_var="DOCKER_EXCLUDE_CONTAINERS",
            default="",
            caster=str,
        ),
        "docker_source_alias": resolve_setting(
            db_settings,
            key="docker_source_alias",
            env_var="DOCKER_SOURCE_ALIAS",
            default="docker",
            caster=str,
        ),
        "trusted_proxies": resolve_setting(
            db_settings,
            key="trusted_proxies",
            env_var="TRUSTED_PROXIES",
            default="",
            caster=str,
        ),
        # Legacy Aliases: TRUST_DOCKER_NETWORKS and TRUST_DOCKER_GATEWAY were used in
        # early versions as alternates to TRUST_DOCKER_PROXIES. Retained silently for
        # backward compatibility with existing docker-compose configurations.
        "trust_docker_proxies": resolve_setting(
            db_settings,
            key="trust_docker_proxies",
            env_var="TRUST_DOCKER_PROXIES",
            env_aliases=["TRUST_DOCKER_NETWORKS", "TRUST_DOCKER_GATEWAY"],
            default=False,
            caster=_cast_bool,
        ),
        "cookie_secure": resolve_setting(
            db_settings,
            key="cookie_secure",
            env_var="COOKIE_SECURE",
            default=False,
            caster=_cast_bool,
        ),
        "syslog_max_tcp_connections": resolve_setting(
            db_settings,
            key="syslog_max_tcp_connections",
            env_var="SYSLOG_MAX_TCP_CONNECTIONS",
            default=250,
            caster=_cast_syslog_max_connections,
        ),
        "syslog_tcp_inactivity_timeout": resolve_setting(
            db_settings,
            key="syslog_tcp_inactivity_timeout",
            env_var="SYSLOG_TCP_INACTIVITY_TIMEOUT",
            default=0.0,
            caster=_cast_syslog_inactivity_timeout,
        ),
        "enable_mcp": resolve_setting(
            db_settings,
            key="enable_mcp",
            env_var="LOGSHED_ENABLE_MCP",
            default=False,
            caster=_cast_bool,
        ),
    }


_cached_db_settings: Optional[dict[str, str]] = None
_cached_resolved_settings: Optional[dict[str, Any]] = None
_cache_timestamp: float = 0.0
_cache_lock = threading.Lock()
SETTINGS_CACHE_TTL = 10.0


def invalidate_settings_cache() -> None:
    """Clear cached system settings, forcing reload on next access."""
    global _cached_db_settings, _cached_resolved_settings, _cache_timestamp
    with _cache_lock:
        _cached_db_settings = None
        _cached_resolved_settings = None
        _cache_timestamp = 0.0


def _load_db_settings_sync() -> dict[str, str]:
    db_p = get_db_path()
    if not db_p.exists():
        return {}
    try:
        conn = sqlite3.connect(f"file:{db_p}?mode=ro", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            return get_all_system_settings(conn)
        finally:
            conn.close()
    except Exception:
        try:
            conn = sqlite3.connect(db_p, timeout=5.0)
            conn.row_factory = sqlite3.Row
            try:
                return get_all_system_settings(conn)
            finally:
                conn.close()
        except Exception:
            return {}


def get_cached_system_settings() -> dict[str, Any]:
    """
    Returns the cached dictionary of effective system settings.
    Refreshes database settings and resolves settings if expired (TTL 10s)
    or explicitly invalidated, returning the cached resolved dictionary.
    """
    global _cached_db_settings, _cached_resolved_settings, _cache_timestamp
    now = time.monotonic()
    with _cache_lock:
        if (
            _cached_resolved_settings is None
            or _cached_db_settings is None
            or (now - _cache_timestamp) >= SETTINGS_CACHE_TTL
        ):
            _cached_db_settings = _load_db_settings_sync()
            _cached_resolved_settings = resolve_all_system_settings(_cached_db_settings)
            _cache_timestamp = now
        return dict(_cached_resolved_settings)


def get_cached_setting(key: str, default: Any = None) -> Any:
    """Convenience helper to retrieve a single effective system setting."""
    return get_cached_system_settings().get(key, default)



