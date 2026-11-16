"""
The whole configuration as one JSON file: export, reading a file back, and the plan of an import.

A file holds every setting and list a user configures (settings, notification targets, Docker
hosts, host aliases, alert and drop rules, saved views, maintenance schedules) and never logs or
history. Stored secrets (AI keys, notification URLs, Docker TLS client keys) and API tokens are
left out unless the user asks for them with a password: they then travel in one block encrypted
with a key derived from that password (scrypt, then AES-256-GCM), so the file is no more readable
than the password is strong, and the server's own `.secret_key` never leaves it.

An import matches each item with one already on this server (by name; host aliases by address, API
tokens by their hash, the local Docker host by being the local one) and the file wins: a match is
replaced, anything else is added, and items only on this server are kept. The routes in
`api/config_transfer.py` apply the plan through the same handlers as the web UI, so every check and
follow-up (alias renames, collector and cache reloads) is the same as for a change made by hand.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.ai_providers import AI_PROVIDERS, normalize_provider_id, stored_provider_setting
from app.core.config import get_all_system_settings, parse_bool
from app.core.security import decrypt_value

FORMAT = "logshed-config"
FORMAT_VERSION = 1

# Applied in this order: rules and the daily digest name their notification target, and a host
# alias may not take a name a Docker host uses, so targets and Docker hosts come first.
SECTIONS: tuple[str, ...] = (
    "notification_targets",
    "docker_hosts",
    "host_aliases",
    "alert_rules",
    "drop_rules",
    "saved_views",
    "maintenance_schedules",
    "api_tokens",
    "settings",
)

# Most items one section of a file may hold
MAX_SECTION_ITEMS = 5000

# The password-derived key: scrypt at the cost LogShed uses for a passphrase key (128 MiB, about
# half a second), so each guess at a stolen file's password costs the same.
SCRYPT_N = 2**17
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_MAXMEM = 256 * 1024 * 1024
_AAD = b"logshed-config-v1"
MIN_PASSWORD_LENGTH = 8

# Stored settings a file carries, with how each is read from its stored text. Docker's three
# settings belong to the local Docker host (section docker_hosts); maintenance_until, digest run
# times and other values LogShed keeps for itself are not configuration.
_SETTING_TYPES: dict[str, type] = {
    "ai_enabled": bool,
    "ai_provider": str,
    "ai_system_prompt": str,
    "retention_days": int,
    "internal_log_level": str,
    "check_for_updates": bool,
    "backup_before_migration": bool,
    "ai_timeout": float,
    "ai_thinking_level": str,
    "app_url": str,
    "allow_private_notification_targets": bool,
    "trusted_proxies": str,
    "trust_docker_proxies": bool,
    "cookie_secure": bool,
    "syslog_max_tcp_connections": int,
    "syslog_tcp_inactivity_timeout": float,
    "enable_mcp": bool,
    "daily_digest_enabled": bool,
    "daily_digest_schedule_time": str,
}
_PROVIDER_FIELDS = ("ai_model", "ai_fallback_models", "ai_base_url")
# Settings that depend on how a server is reached: a preview names them when the file changes them
_HOW_REACHED = ("app_url", "trusted_proxies", "trust_docker_proxies", "cookie_secure")


class ConfigFileError(ValueError):
    """The file cannot be read as a LogShed configuration (the message says why, for the user)."""


class WrongPasswordError(ValueError):
    """The password does not open the file's secrets."""


# ---------------------------------------------------------------------------
# Secrets block
# ---------------------------------------------------------------------------

def _derive_key(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=SCRYPT_MAXMEM, dklen=32)


def encrypt_secrets(secrets: dict[str, Any], password: str) -> dict[str, Any]:
    """The secrets as the file's encrypted block. Runs scrypt: call it off the event loop."""
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = _derive_key(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    ciphertext = AESGCM(key).encrypt(nonce, json.dumps(secrets).encode("utf-8"), _AAD)
    return {
        "kdf": "scrypt",
        "n": SCRYPT_N,
        "r": SCRYPT_R,
        "p": SCRYPT_P,
        "salt": base64.b64encode(salt).decode("ascii"),
        "cipher": "aes-256-gcm",
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }


def decrypt_secrets(block: Any, password: str) -> dict[str, Any]:
    """The secrets in a file's encrypted block. Runs scrypt: call it off the event loop."""
    if not isinstance(block, dict) or block.get("kdf") != "scrypt" or block.get("cipher") != "aes-256-gcm":
        raise ConfigFileError("The file's secrets are in a form this version of LogShed cannot read.")
    try:
        n, r, p = int(block["n"]), int(block["r"]), int(block["p"])
        salt = base64.b64decode(block["salt"], validate=True)
        nonce = base64.b64decode(block["nonce"], validate=True)
        ciphertext = base64.b64decode(block["ciphertext"], validate=True)
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigFileError("The file's secrets block is damaged.") from exc
    # A file sets its own cost: refuse one that would take far more memory or time than ours
    # Bounds keep scrypt within SCRYPT_MAXMEM (128 x r x n bytes) and the cipher's nonce size
    if not (2**10 <= n <= 2**17 and n & (n - 1) == 0 and 1 <= r <= 8 and 1 <= p <= 4) or len(nonce) != 12:
        raise ConfigFileError("The file's secrets use key settings LogShed does not accept.")
    try:
        plain = AESGCM(_derive_key(password, salt, n, r, p)).decrypt(nonce, ciphertext, _AAD)
    except InvalidTag as exc:
        raise WrongPasswordError("The password does not open this file's secrets.") from exc
    except ValueError as exc:
        raise ConfigFileError("The file's secrets block is damaged.") from exc
    try:
        secrets = json.loads(plain)
    except ValueError as exc:
        raise ConfigFileError("The file's secrets block is damaged.") from exc
    if not isinstance(secrets, dict):
        raise ConfigFileError("The file's secrets block is damaged.")
    return secrets


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def _decrypt_or_none(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        return decrypt_value(value)
    except Exception:
        # A value this server cannot read (key changed) is left out, never exported as ciphertext
        return None


def _typed_setting(kind: type, raw: str) -> Any:
    if kind is bool:
        return parse_bool(raw, False)
    if kind is int:
        return int(raw)
    if kind is float:
        return float(raw)
    return raw


def _export_settings(conn: sqlite3.Connection, stored: dict[str, str]) -> tuple[dict[str, Any], dict[str, str]]:
    """The settings section, and the AI keys for the secrets block (provider -> key)."""
    out: dict[str, Any] = {}
    for key, kind in _SETTING_TYPES.items():
        raw = stored.get(key)
        if raw is None or (raw == "" and kind is not str):
            continue
        try:
            out[key] = _typed_setting(kind, raw)
        except ValueError:
            continue

    channel_raw = (stored.get("daily_digest_channel_id") or "").strip()
    if channel_raw:
        row = conn.execute("SELECT name FROM notification_channels WHERE id = ?", (channel_raw,)).fetchone()
        out["daily_digest_target"] = row[0] if row else None
    elif "daily_digest_channel_id" in stored:
        out["daily_digest_target"] = None

    active = normalize_provider_id(stored.get("ai_provider"))
    providers: dict[str, dict[str, str]] = {}
    keys: dict[str, str] = {}
    for provider in AI_PROVIDERS:
        fields = {
            name: stored_provider_setting(stored, provider, name, active)
            for name in _PROVIDER_FIELDS
        }
        fields = {k: v for k, v in fields.items() if v}
        if fields:
            providers[provider] = fields
        # get_all_system_settings has already decrypted stored keys
        key = stored_provider_setting(stored, provider, "ai_api_key", active)
        if key:
            keys[provider] = key
    if providers:
        out["ai_providers"] = providers
    return out, keys


def build_export(conn: sqlite3.Connection, include_secrets: bool, app_version: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    The configuration file without its secrets block, and the secrets (empty when left out) for
    encrypt_secrets. Reads the database only.
    """
    conn.row_factory = sqlite3.Row
    stored = get_all_system_settings(conn)
    settings, ai_keys = _export_settings(conn, stored)
    secrets: dict[str, Any] = {}

    targets = []
    target_urls: dict[str, str] = {}
    for row in conn.execute("SELECT name, url, is_enabled FROM notification_channels ORDER BY id"):
        targets.append({"name": row["name"], "is_enabled": bool(row["is_enabled"])})
        url = _decrypt_or_none(row["url"])
        if url:
            target_urls[row["name"]] = url

    docker_hosts = []
    client_keys: dict[str, str] = {}
    for row in conn.execute("SELECT * FROM docker_endpoints ORDER BY id"):
        docker_hosts.append({
            "name": row["name"],
            "local": row["id"] == 1,
            "url": row["url"],
            "source_alias": row["source_alias"],
            "enabled": bool(row["enabled"]),
            "tls_enabled": bool(row["tls_enabled"]),
            "verify_hostname": bool(row["verify_hostname"]),
            "ca_cert": row["ca_cert"] or None,
            "client_cert": row["client_cert"] or None,
            "has_client_key": bool(row["client_key"]),
            "exclude_containers": row["exclude_containers"] or "",
        })
        key = _decrypt_or_none(row["client_key"])
        if key:
            client_keys[row["name"]] = key

    aliases = [
        {"ip": row["ip"], "alias": row["alias"], "notes": row["notes"]}
        for row in conn.execute("SELECT ip, alias, notes FROM host_aliases ORDER BY ip")
    ]

    channel_names = {row[0]: row[1] for row in conn.execute("SELECT id, name FROM notification_channels")}
    alert_rules = []
    for row in conn.execute("SELECT * FROM alert_rules ORDER BY display_order, id"):
        alert_rules.append({
            "name": row["name"],
            "rule_type": row["rule_type"],
            "target": channel_names.get(row["channel_id"]) if row["channel_id"] is not None else None,
            "filter_app": row["filter_app"],
            "filter_host": row["filter_host"],
            "filter_severity": row["filter_severity"],
            "match_pattern": row["match_pattern"],
            "is_regex": bool(row["is_regex"]),
            "threshold_count": row["threshold_count"],
            "window_seconds": row["window_seconds"],
            "cooldown_seconds": row["cooldown_seconds"],
            "ai_enrichment": bool(row["ai_enrichment"]),
            "is_enabled": bool(row["is_enabled"]),
        })

    drop_rules = [
        {
            "name": row["name"],
            "source_pattern": row["source_pattern"],
            "app_pattern": row["app_pattern"],
            "message_pattern": row["message_pattern"],
            "is_regex": bool(row["is_regex"]),
            "is_enabled": bool(row["is_enabled"]),
            "severity_threshold": row["severity_threshold"],
        }
        for row in conn.execute("SELECT * FROM drop_rules ORDER BY display_order, id")
    ]

    saved_views = []
    for row in conn.execute("SELECT name, query_params, is_pinned FROM saved_views ORDER BY id"):
        try:
            params = json.loads(row["query_params"] or "{}")
        except ValueError:
            continue
        saved_views.append({"name": row["name"], "query_params": params, "is_pinned": bool(row["is_pinned"])})

    schedules = []
    try:
        stored_schedules = json.loads(stored.get("maintenance_schedules") or "[]")
    except ValueError:
        stored_schedules = []
    for item in stored_schedules if isinstance(stored_schedules, list) else []:
        if isinstance(item, dict):
            schedules.append({k: v for k, v in item.items() if k not in ("id", "is_active", "next_run")})

    sections: dict[str, Any] = {
        "settings": settings,
        "notification_targets": targets,
        "docker_hosts": docker_hosts,
        "host_aliases": aliases,
        "alert_rules": alert_rules,
        "drop_rules": drop_rules,
        "saved_views": saved_views,
        "maintenance_schedules": schedules,
    }

    if include_secrets:
        tokens = [
            {
                "name": row["name"],
                "token_hash": row["token_hash"],
                "token_prefix": row["token_prefix"],
                "scopes": json.loads(row["scopes"] or "[]"),
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
                "created_by": row["created_by"],
            }
            for row in conn.execute("SELECT * FROM api_tokens ORDER BY id")
        ]
        secrets = {
            "ai_api_keys": ai_keys,
            "notification_urls": target_urls,
            "docker_client_keys": client_keys,
            "api_tokens": tokens,
        }

    document = {
        "format": FORMAT,
        "version": FORMAT_VERSION,
        "logshed_version": app_version,
        "exported_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "secrets_included": include_secrets,
        "sections": sections,
    }
    return document, secrets


# ---------------------------------------------------------------------------
# Reading a file
# ---------------------------------------------------------------------------

@dataclass
class ConfigFile:
    """A configuration file read back, with its secrets when the password opened them."""
    sections: dict[str, Any]
    has_secrets: bool
    secrets: Optional[dict[str, Any]] = None
    logshed_version: Optional[str] = None
    exported_at: Optional[str] = None


def read_file(document: Any, password: Optional[str]) -> ConfigFile:
    """
    Checks a file's form and opens its secrets with the password. No password, or an empty one,
    reads the file without its secrets. Runs scrypt when there is a password: call it off the
    event loop.
    """
    if not isinstance(document, dict) or document.get("format") != FORMAT:
        raise ConfigFileError("This is not a LogShed configuration file.")
    version = document.get("version")
    if not isinstance(version, int) or version < 1:
        raise ConfigFileError("The file has no format version.")
    if version > FORMAT_VERSION:
        raise ConfigFileError(
            "The file was exported by a newer version of LogShed. Update this server before importing it."
        )
    sections = document.get("sections")
    if not isinstance(sections, dict):
        raise ConfigFileError("The file has no sections.")
    for name, content in sections.items():
        if name == "settings":
            if not isinstance(content, dict):
                raise ConfigFileError("The file's settings are not an object.")
        elif name in SECTIONS:
            if not isinstance(content, list):
                raise ConfigFileError(f"The file's {name} section is not a list.")
            if len(content) > MAX_SECTION_ITEMS:
                raise ConfigFileError(f"The file's {name} section holds more than {MAX_SECTION_ITEMS} items.")

    block = document.get("secrets")
    secrets = None
    if block is not None and password:
        secrets = decrypt_secrets(block, password)
    return ConfigFile(
        sections={k: v for k, v in sections.items() if k in SECTIONS},
        has_secrets=block is not None,
        secrets=secrets,
        logshed_version=document.get("logshed_version") if isinstance(document.get("logshed_version"), str) else None,
        exported_at=document.get("exported_at") if isinstance(document.get("exported_at"), str) else None,
    )


# ---------------------------------------------------------------------------
# Import plan
# ---------------------------------------------------------------------------

@dataclass
class PlanItem:
    """One item of a section: what the import does with it."""
    label: str
    action: str  # "add", "replace", "unchanged" (a match already the same) or "skip"
    data: dict[str, Any] = field(default_factory=dict)
    existing_id: Any = None
    note: Optional[str] = None


@dataclass
class SectionPlan:
    name: str
    items: list[PlanItem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def count(self, action: str) -> int:
        return sum(1 for item in self.items if item.action == action)


def _item_dicts(cfg: ConfigFile, section: str, plan: SectionPlan) -> list[dict[str, Any]]:
    items = []
    for index, raw in enumerate(cfg.sections.get(section) or []):
        if isinstance(raw, dict):
            items.append(raw)
        else:
            plan.items.append(PlanItem(label=f"Item {index + 1}", action="skip", note="Not an object."))
    return items


def _name_of(raw: dict[str, Any]) -> str:
    value = raw.get("name")
    return value.strip() if isinstance(value, str) else ""


def _secret_map(cfg: ConfigFile, key: str) -> dict[str, Any]:
    value = (cfg.secrets or {}).get(key)
    return value if isinstance(value, dict) else {}


def _plan_targets(conn: sqlite3.Connection, cfg: ConfigFile) -> SectionPlan:
    plan = SectionPlan("notification_targets")
    existing = {row[1].strip().lower(): row[0] for row in conn.execute("SELECT id, name FROM notification_channels")}
    urls = _secret_map(cfg, "notification_urls")
    for raw in _item_dicts(cfg, "notification_targets", plan):
        name = _name_of(raw)
        if not name:
            plan.items.append(PlanItem(label="(no name)", action="skip", note="A target needs a name."))
            continue
        url = urls.get(raw.get("name")) if isinstance(urls.get(raw.get("name")), str) else None
        data = {"name": name, "is_enabled": bool(raw.get("is_enabled", True)), "url": url}
        match = existing.get(name.lower())
        if match is not None:
            plan.items.append(PlanItem(label=name, action="replace", data=data, existing_id=match,
                                       note=None if url else "Keeps this server's URL."))
        elif url:
            plan.items.append(PlanItem(label=name, action="add", data=data))
        else:
            plan.items.append(PlanItem(label=name, action="skip", data=data,
                                       note="Its URL is a secret: import the file with its password to add it."))
    return plan


def _plan_docker_hosts(conn: sqlite3.Connection, cfg: ConfigFile) -> SectionPlan:
    plan = SectionPlan("docker_hosts")
    existing = {row[1]: row[0] for row in conn.execute("SELECT id, name FROM docker_endpoints")}
    keys = _secret_map(cfg, "docker_client_keys")
    for raw in _item_dicts(cfg, "docker_hosts", plan):
        name = _name_of(raw)
        local = bool(raw.get("local"))
        if not name and not local:
            plan.items.append(PlanItem(label="(no name)", action="skip", note="A Docker host needs a name."))
            continue
        key = keys.get(raw.get("name")) if isinstance(keys.get(raw.get("name")), str) else None
        data = {
            "name": name,
            "url": raw.get("url"),
            "source_alias": raw.get("source_alias") or "docker",
            "enabled": bool(raw.get("enabled", True)),
            "tls_enabled": bool(raw.get("tls_enabled", False)),
            "verify_hostname": bool(raw.get("verify_hostname", True)),
            "ca_cert": raw.get("ca_cert") or None,
            "client_cert": raw.get("client_cert") or None,
            "exclude_containers": raw.get("exclude_containers") or "",
        }
        if key:
            data["client_key"] = key
        missing_key = bool(raw.get("has_client_key")) and not key
        if local:
            # The local host is always host 1 and keeps its name, and its address: that comes from
            # this server's own DOCKER_HOST (a socket, or a socket proxy beside it)
            data.pop("name")
            data.pop("url")
            plan.items.append(PlanItem(label="Local Docker host", action="replace", data=data, existing_id=1,
                                       note="Keeps this server's client key." if missing_key else None))
            continue
        match = existing.get(name)
        if match is not None:
            plan.items.append(PlanItem(label=name, action="replace", data=data, existing_id=match,
                                       note="Keeps this server's client key." if missing_key else None))
        else:
            note = None
            if missing_key:
                data["enabled"] = False
                note = "Added turned off: its TLS client key is a secret. Add the key in Settings > Sources, then turn it on."
            plan.items.append(PlanItem(label=name, action="add", data=data, note=note))
    return plan


def _plan_aliases(conn: sqlite3.Connection, cfg: ConfigFile) -> SectionPlan:
    plan = SectionPlan("host_aliases")
    existing = {row[0] for row in conn.execute("SELECT ip FROM host_aliases")}
    for raw in _item_dicts(cfg, "host_aliases", plan):
        ip = raw.get("ip").strip() if isinstance(raw.get("ip"), str) else ""
        alias = raw.get("alias").strip() if isinstance(raw.get("alias"), str) else ""
        if not ip or not alias:
            plan.items.append(PlanItem(label=ip or "(no address)", action="skip", note="An alias needs an address and a name."))
            continue
        data = {"ip": ip, "alias": alias, "notes": raw.get("notes") if isinstance(raw.get("notes"), str) else None}
        plan.items.append(PlanItem(label=f"{ip} = {alias}", action="replace" if ip in existing else "add", data=data,
                                   existing_id=ip if ip in existing else None))
    return plan


def _plan_by_name(conn: sqlite3.Connection, cfg: ConfigFile, section: str, table: str) -> SectionPlan:
    plan = SectionPlan(section)
    existing = {row[1].strip().lower(): row[0] for row in conn.execute(f"SELECT id, name FROM {table}") if row[1]}
    for raw in _item_dicts(cfg, section, plan):
        name = _name_of(raw)
        if not name:
            plan.items.append(PlanItem(label="(no name)", action="skip", note="It needs a name."))
            continue
        match = existing.get(name.lower())
        plan.items.append(PlanItem(label=name, action="replace" if match is not None else "add", data=dict(raw),
                                   existing_id=match))
    return plan


def _plan_alert_rules(
    conn: sqlite3.Connection, cfg: ConfigFile, target_names: set[str], file_targets: set[str]
) -> SectionPlan:
    plan = _plan_by_name(conn, cfg, "alert_rules", "alert_rules")
    for item in plan.items:
        target = item.data.get("target") if item.data else None
        if not isinstance(target, str) or not target.strip() or target.strip().lower() in target_names:
            continue
        if target.strip().lower() in file_targets:
            item.note = f"Its target '{target}' is added by this file: import Notification targets too, or the rule is imported turned off."
        else:
            item.note = f"Its target '{target}' will not be on this server, so it is imported turned off."
    return plan


def _drop_rule_key(raw: dict[str, Any]) -> tuple:
    def text(v: Any) -> str:
        return v.strip() if isinstance(v, str) else ""
    return (
        text(raw.get("source_pattern")).lower(),
        text(raw.get("app_pattern")).lower(),
        text(raw.get("message_pattern")),
        bool(raw.get("is_regex")),
        raw.get("severity_threshold"),
    )


def _plan_drop_rules(conn: sqlite3.Connection, cfg: ConfigFile) -> SectionPlan:
    plan = SectionPlan("drop_rules")
    conn.row_factory = sqlite3.Row
    by_name: dict[str, int] = {}
    by_criteria: dict[tuple, int] = {}
    for row in conn.execute("SELECT * FROM drop_rules"):
        if row["name"] and row["name"].strip():
            by_name[row["name"].strip().lower()] = row["id"]
        else:
            by_criteria[_drop_rule_key(dict(row))] = row["id"]
    for raw in _item_dicts(cfg, "drop_rules", plan):
        name = _name_of(raw)
        if name:
            match = by_name.get(name.lower())
            label = name
        else:
            # A drop rule without a name is the same rule when it drops the same logs
            match = by_criteria.get(_drop_rule_key(raw))
            label = f"(no name) {raw.get('message_pattern') or '*'}"
        plan.items.append(PlanItem(label=label, action="replace" if match is not None else "add", data=dict(raw),
                                   existing_id=match))
    return plan


def _plan_schedules(conn: sqlite3.Connection, cfg: ConfigFile) -> SectionPlan:
    plan = SectionPlan("maintenance_schedules")
    row = conn.execute("SELECT value FROM system_settings WHERE key = 'maintenance_schedules'").fetchone()
    try:
        current = json.loads(row[0]) if row and row[0] else []
    except ValueError:
        current = []
    existing = {
        s["name"].strip().lower(): s.get("id")
        for s in current if isinstance(s, dict) and isinstance(s.get("name"), str)
    }
    for raw in _item_dicts(cfg, "maintenance_schedules", plan):
        name = _name_of(raw)
        if not name:
            plan.items.append(PlanItem(label="(no name)", action="skip", note="A schedule needs a name."))
            continue
        key = name.lower()
        plan.items.append(PlanItem(label=name, action="replace" if key in existing else "add", data=dict(raw),
                                   existing_id=existing.get(key)))
    return plan


def _parse_time(value: str) -> Optional[datetime.datetime]:
    try:
        return datetime.datetime.fromisoformat(value)
    except ValueError:
        return None


def _plan_tokens(conn: sqlite3.Connection, cfg: ConfigFile) -> SectionPlan:
    plan = SectionPlan("api_tokens")
    tokens = (cfg.secrets or {}).get("api_tokens")
    if not isinstance(tokens, list):
        return plan
    existing = {row[1]: row[0] for row in conn.execute("SELECT id, token_hash FROM api_tokens")}
    for raw in tokens:
        if not isinstance(raw, dict):
            continue
        name = _name_of(raw)
        token_hash = raw.get("token_hash")
        expires_at = raw.get("expires_at")
        # A token whose expiry cannot be read would never expire
        bad_expiry = expires_at is not None and (not isinstance(expires_at, str) or _parse_time(expires_at) is None)
        if not name or not isinstance(token_hash, str) or len(token_hash) != 64 or bad_expiry:
            plan.items.append(PlanItem(label=name or "(no name)", action="skip", note="The token is damaged."))
            continue
        match = existing.get(token_hash)
        plan.items.append(PlanItem(label=name, action="replace" if match is not None else "add", data=dict(raw),
                                   existing_id=match))
    return plan


def _plan_settings(conn: sqlite3.Connection, cfg: ConfigFile) -> SectionPlan:
    plan = SectionPlan("settings")
    wanted = cfg.sections.get("settings")
    if not isinstance(wanted, dict):
        return plan
    current, current_keys = _export_settings(conn, get_all_system_settings(conn))
    ai_keys = _secret_map(cfg, "ai_api_keys")
    for key, value in wanted.items():
        if key == "ai_providers":
            if not isinstance(value, dict):
                continue
            for provider, fields in value.items():
                if not isinstance(fields, dict) or normalize_provider_id(provider) not in AI_PROVIDERS:
                    plan.notes.append(f"AI provider '{provider}' is not one this version knows, so it is left out.")
                    continue
                now = (current.get("ai_providers") or {}).get(provider, {})
                for name in _PROVIDER_FIELDS:
                    if name in fields and fields[name] != now.get(name, ""):
                        plan.items.append(PlanItem(label=f"{provider} {name}", action="replace"))
            continue
        if key not in _SETTING_TYPES and key != "daily_digest_target":
            plan.notes.append(f"Setting '{key}' is not one this version knows, so it is left out.")
            continue
        if current.get(key) != value:
            plan.items.append(PlanItem(label=key, action="replace"))
    reached = [i.label for i in plan.items if i.label in _HOW_REACHED]
    if reached:
        plan.notes.append(
            f"{', '.join(reached)} describe how the old server was reached, and a saved setting is used instead of "
            "the environment variable. Leave Settings unticked if this server is reached another way: with Force "
            "Secure Session Cookies on, signing in over plain HTTP fails."
        )
    for provider, key in ai_keys.items():
        if normalize_provider_id(provider) in AI_PROVIDERS and key != current_keys.get(normalize_provider_id(provider)):
            plan.items.append(PlanItem(label=f"{provider} API key", action="replace"))
    return plan


def _same(wanted: dict[str, Any], current: Optional[dict[str, Any]], keys: Optional[tuple[str, ...]] = None) -> bool:
    if current is None:
        return False
    # Without a list, every key either side has: a row stored before a field existed has no value
    # for it, and a file that sets one is a change, not the same
    return all(wanted.get(k) == current.get(k) for k in (keys or tuple(set(current) | set(wanted))))


def _mark_unchanged(plans: dict[str, SectionPlan], current: dict[str, Any], secrets: dict[str, Any]) -> None:
    """Marks each replace whose item already matches this server's as "unchanged" (nothing to do)."""
    def by(section: str, key) -> dict[Any, dict[str, Any]]:
        return {key(item): item for item in current.get(section, [])}

    urls = secrets.get("notification_urls", {})
    targets = by("notification_targets", lambda t: t["name"].strip().lower())
    for item in plans["notification_targets"].items:
        cur = targets.get(item.data.get("name", "").lower()) if item.action == "replace" else None
        url = item.data.get("url")
        if _same(item.data, cur, ("name", "is_enabled")) and (url is None or url == urls.get(cur["name"])):
            item.action, item.note = "unchanged", None

    client_keys = secrets.get("docker_client_keys", {})
    hosts = by("docker_hosts", lambda d: ("local",) if d["local"] else d["name"])
    host_fields = ("source_alias", "enabled", "tls_enabled", "verify_hostname", "ca_cert", "client_cert", "exclude_containers")
    for item in plans["docker_hosts"].items:
        if item.action != "replace":
            continue
        cur = hosts.get(("local",) if item.existing_id == 1 else item.data.get("name"))
        key = item.data.get("client_key")
        fields = host_fields if item.existing_id == 1 else ("url", *host_fields)
        if _same(item.data, cur, fields) and (key is None or key == client_keys.get(cur["name"])):
            item.action, item.note = "unchanged", None

    aliases = by("host_aliases", lambda a: a["ip"])
    for item in plans["host_aliases"].items:
        if item.action == "replace" and _same(item.data, aliases.get(item.data["ip"]), ("alias", "notes")):
            item.action = "unchanged"

    for section in ("alert_rules", "saved_views", "maintenance_schedules"):
        named = by(section, lambda r: (r.get("name") or "").strip().lower())
        for item in plans[section].items:
            if item.action == "replace" and _same(item.data, named.get(_name_of(item.data).lower())):
                item.action, item.note = "unchanged", None

    drops = {
        ((d.get("name") or "").strip().lower() or _drop_rule_key(d)): d for d in current.get("drop_rules", [])
    }
    for item in plans["drop_rules"].items:
        key = _name_of(item.data).lower() or _drop_rule_key(item.data)
        if item.action == "replace" and _same(item.data, drops.get(key)):
            item.action = "unchanged"

    tokens = {t["token_hash"]: t for t in secrets.get("api_tokens", [])}
    for item in plans["api_tokens"].items:
        if item.action == "replace" and _same(item.data, tokens.get(item.data.get("token_hash")), ("name", "scopes", "expires_at")):
            item.action = "unchanged"


def plan_import(conn: sqlite3.Connection, cfg: ConfigFile) -> list[SectionPlan]:
    """What an import of every section in the file would do, in the order it is applied."""
    current, current_secrets = build_export(conn, cfg.secrets is not None, "")
    conn.row_factory = sqlite3.Row
    target_names = {row[0].strip().lower() for row in conn.execute("SELECT name FROM notification_channels")}
    targets = _plan_targets(conn, cfg)
    file_targets = {item.data["name"].lower() for item in targets.items if item.action == "add"}
    plans = {
        "notification_targets": targets,
        "docker_hosts": _plan_docker_hosts(conn, cfg),
        "host_aliases": _plan_aliases(conn, cfg),
        "alert_rules": _plan_alert_rules(conn, cfg, target_names, file_targets),
        "drop_rules": _plan_drop_rules(conn, cfg),
        "saved_views": _plan_by_name(conn, cfg, "saved_views", "saved_views"),
        "maintenance_schedules": _plan_schedules(conn, cfg),
        "api_tokens": _plan_tokens(conn, cfg),
        "settings": _plan_settings(conn, cfg),
    }
    _mark_unchanged(plans, current["sections"], current_secrets)
    in_file = set(cfg.sections) | ({"api_tokens"} if cfg.secrets and cfg.secrets.get("api_tokens") else set())
    return [plans[name] for name in SECTIONS if name in in_file]
