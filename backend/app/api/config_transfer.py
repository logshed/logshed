"""
Whole-configuration export and import (Settings > Advanced). The file format and the import plan are
in `services/config_transfer.py`; this module applies a plan through the routes the web UI uses for
each kind of item, so an import is checked and followed up exactly as the same changes made by hand.
"""

import asyncio
import datetime
import json
import logging
import secrets as pysecrets
import sqlite3
import time
from typing import Any, Awaitable, Callable, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, ValidationError

from app.api.deps import get_current_user, run_db_query
from app.core.ai_providers import AI_PROVIDERS, normalize_provider_id
from app.core.config import get_max_retention_days
from app.models import AiProviderConfig, MaintenanceSchedule, MaintenanceSchedulesUpdateRequest, SettingsUpdateRequest
from app.services.config_transfer import (
    MIN_PASSWORD_LENGTH,
    SECTIONS,
    ConfigFile,
    ConfigFileError,
    PlanItem,
    SectionPlan,
    WrongPasswordError,
    build_export,
    encrypt_secrets,
    plan_import,
    read_file,
)
from app.version import APP_VERSION

router = APIRouter(prefix="/config", tags=["Configuration"])
logger = logging.getLogger(__name__)


class ConfigExportRequest(BaseModel):
    include_secrets: bool = False
    # Encrypts the secrets in the file
    password: Optional[str] = Field(None, max_length=1024)
    # The admin password, needed to export secrets: a session alone never reads stored secrets back
    admin_password: Optional[str] = Field(None, max_length=1024)


class ConfigImportRequest(BaseModel):
    file: dict[str, Any]
    # Left out or empty: the file is read without its secrets
    password: Optional[str] = Field(None, max_length=1024)


class ConfigImportApplyRequest(ConfigImportRequest):
    sections: list[str]


class PlanItemResponse(BaseModel):
    label: str
    action: str
    note: Optional[str] = None


class SectionPreview(BaseModel):
    name: str
    add: int
    replace: int
    unchanged: int = 0
    skip: int
    items: list[PlanItemResponse]
    notes: list[str] = []


class ConfigImportPreviewResponse(BaseModel):
    has_secrets: bool
    secrets_read: bool
    logshed_version: Optional[str] = None
    exported_at: Optional[str] = None
    sections: list[SectionPreview]


class ItemFailure(BaseModel):
    label: str
    error: str


class SectionResult(BaseModel):
    name: str
    added: int = 0
    replaced: int = 0
    unchanged: int = 0
    skipped: int = 0
    failed: list[ItemFailure] = []
    notes: list[str] = []


class ConfigImportResponse(BaseModel):
    sections: list[SectionResult]


async def _read(req: ConfigImportRequest) -> ConfigFile:
    try:
        return await asyncio.to_thread(read_file, req.file, req.password or None)
    except WrongPasswordError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ConfigFileError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc


async def _check_admin_password(request: Request, password: str) -> None:
    """Refuses (400) a wrong admin password, counted by the sign-in limit (429 when it is reached)."""
    from app.api.auth import _verify_password_off_loop, get_client_ip
    from app.core.rate_limiter import ip_bucket_key, login_rate_limiter

    limiter_key = ip_bucket_key(get_client_ip(request))
    attempt = login_rate_limiter.begin(limiter_key)
    if attempt is None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many wrong passwords. Try again later.",
            headers={"Retry-After": str(login_rate_limiter.retry_after(limiter_key))},
        )
    valid = False
    try:
        row = await run_db_query(lambda conn: conn.execute("SELECT password_hash FROM admin_auth WHERE id = 1").fetchone())
        valid = bool(row) and await _verify_password_off_loop(row[0], password)
    finally:
        login_rate_limiter.finish(attempt, success=valid)
    if not valid:
        # 400, not 401: the session is still good, only this password is wrong
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The admin password is incorrect.")


@router.post("/export")
async def export_configuration(
    req: ConfigExportRequest, request: Request, user: dict = Depends(get_current_user)
) -> Response:
    """
    The whole configuration as a JSON download. With include_secrets, the admin password is checked
    and the stored secrets and API tokens are added encrypted with the password (at least
    MIN_PASSWORD_LENGTH characters).
    """
    if req.include_secrets and len(req.password or "") < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"A password of at least {MIN_PASSWORD_LENGTH} characters is needed to include secrets.",
        )
    if req.include_secrets:
        await _check_admin_password(request, req.admin_password or "")
    document, secrets = await run_db_query(lambda conn: build_export(conn, req.include_secrets, APP_VERSION))
    if req.include_secrets:
        document["secrets"] = await asyncio.to_thread(encrypt_secrets, secrets, req.password or "")
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    logger.info("Configuration exported (%s secrets).", "with" if req.include_secrets else "without")
    return Response(
        content=json.dumps(document, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="logshed-config-{stamp}.json"'},
    )


def _preview(plan: SectionPlan) -> SectionPreview:
    return SectionPreview(
        name=plan.name,
        add=plan.count("add"),
        replace=plan.count("replace"),
        unchanged=plan.count("unchanged"),
        skip=plan.count("skip"),
        items=[PlanItemResponse(label=i.label, action=i.action, note=i.note) for i in plan.items],
        notes=plan.notes,
    )


@router.post("/import/preview", response_model=ConfigImportPreviewResponse)
async def preview_configuration_import(
    req: ConfigImportRequest, user: dict = Depends(get_current_user)
) -> ConfigImportPreviewResponse:
    """What importing each section of the file would add, replace and skip. Changes nothing."""
    cfg = await _read(req)
    plans = await run_db_query(lambda conn: plan_import(conn, cfg))
    return ConfigImportPreviewResponse(
        has_secrets=cfg.has_secrets,
        secrets_read=cfg.secrets is not None,
        logshed_version=cfg.logshed_version,
        exported_at=cfg.exported_at,
        sections=[_preview(p) for p in plans],
    )


def _error_text(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        return str(exc.detail)
    if isinstance(exc, ValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()))
        msg = str(first.get("msg", "invalid value"))
        return f"{where}: {msg}" if where else msg
    return str(exc) or exc.__class__.__name__


# What one item of a file can raise: the route's own refusal, a value the models refuse, a duplicate
# (the same name or token twice in a file). Reported for that item; the import goes on.
_ITEM_ERRORS = (HTTPException, ValidationError, ValueError, TypeError, sqlite3.Error)


async def _apply_items(
    plan: SectionPlan, result: SectionResult, apply_one: Callable[[PlanItem], Awaitable[None]]
) -> None:
    for item in plan.items:
        if item.action == "skip":
            result.skipped += 1
            continue
        if item.action == "unchanged":
            result.unchanged += 1
            continue
        try:
            await apply_one(item)
        except _ITEM_ERRORS as exc:
            result.failed.append(ItemFailure(label=item.label, error=_error_text(exc)))
            continue
        if item.action == "add":
            result.added += 1
        else:
            result.replaced += 1
        if item.note:
            result.notes.append(f"{item.label}: {item.note}")


def _blank(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value.strip() else None


async def _apply_targets(plan: SectionPlan, result: SectionResult, user: dict, _bg: BackgroundTasks) -> None:
    from app.api.notifications import create_notification_channel, update_notification_channel
    from app.models import NotificationChannelCreate, NotificationChannelUpdate

    async def one(item: PlanItem) -> None:
        d = item.data
        if item.action == "add":
            await create_notification_channel(
                NotificationChannelCreate(name=d["name"], url=d["url"], is_enabled=d["is_enabled"]), user=user
            )
        else:
            fields: dict[str, Any] = {"name": d["name"], "is_enabled": d["is_enabled"]}
            if d.get("url"):
                fields["url"] = d["url"]
            await update_notification_channel(item.existing_id, NotificationChannelUpdate(**fields), user=user)

    await _apply_items(plan, result, one)


async def _apply_docker_hosts(plan: SectionPlan, result: SectionResult, user: dict, bg: BackgroundTasks) -> None:
    from app.api.docker_hosts import (
        DockerEndpointCreateRequest,
        DockerEndpointUpdateRequest,
        create_docker_endpoint,
        update_docker_endpoint,
    )

    async def one(item: PlanItem) -> None:
        d = dict(item.data)
        if item.action == "add":
            await create_docker_endpoint(DockerEndpointCreateRequest(**d), background_tasks=bg, user=user)
        else:
            # Replacing: certificates the file has none of are cleared ("" clears, absent keeps)
            d["ca_cert"] = d.get("ca_cert") or ""
            d["client_cert"] = d.get("client_cert") or ""
            await update_docker_endpoint(
                item.existing_id, DockerEndpointUpdateRequest(**d), background_tasks=bg, user=user
            )

    await _apply_items(plan, result, one)


async def _apply_aliases(plan: SectionPlan, result: SectionResult, user: dict, bg: BackgroundTasks) -> None:
    from app.api.aliases import create_or_update_alias
    from app.models import HostAliasCreate

    async def one(item: PlanItem) -> None:
        await create_or_update_alias(HostAliasCreate(**item.data), background_tasks=bg, user=user)

    await _apply_items(plan, result, one)


def _channel_ids(conn) -> dict[str, int]:
    return {row[1].strip().lower(): row[0] for row in conn.execute("SELECT id, name FROM notification_channels")}


async def _apply_alert_rules(plan: SectionPlan, result: SectionResult, user: dict, _bg: BackgroundTasks) -> None:
    from app.api.alerts import create_alert_rule, update_alert_rule
    from app.models import AlertRuleCreate, AlertRuleUpdate

    channels = await run_db_query(_channel_ids)
    names = (
        "rule_type", "filter_app", "filter_host", "filter_severity", "match_pattern", "is_regex",
        "threshold_count", "window_seconds", "cooldown_seconds", "ai_enrichment", "is_enabled",
    )

    async def one(item: PlanItem) -> None:
        d = item.data
        fields: dict[str, Any] = {"name": d["name"].strip()}
        fields.update({k: d[k] for k in names if k in d})
        target = _blank(d.get("target"))
        channel_id = channels.get(target.strip().lower()) if target else None
        if target and channel_id is None:
            # Not sent to every target instead of the one it named: kept off until one is chosen
            fields["is_enabled"] = False
            item.note = f"Turned off: its target '{target}' is not on this server."
        fields["channel_id"] = channel_id
        if item.action == "add":
            await create_alert_rule(AlertRuleCreate(**fields), user=user)
        else:
            await update_alert_rule(item.existing_id, AlertRuleUpdate(**fields), user=user)

    await _apply_items(plan, result, one)


async def _apply_drop_rules(plan: SectionPlan, result: SectionResult, user: dict, _bg: BackgroundTasks) -> None:
    from app.api.drop_rules import create_drop_rule, update_drop_rule
    from app.models import DropRuleCreate, DropRuleUpdate

    names = ("name", "source_pattern", "app_pattern", "message_pattern", "is_regex", "is_enabled", "severity_threshold")

    async def one(item: PlanItem) -> None:
        fields = {k: item.data[k] for k in names if k in item.data}
        if item.action == "add":
            await create_drop_rule(DropRuleCreate(**fields), user=user)
        else:
            await update_drop_rule(item.existing_id, DropRuleUpdate(**fields), user=user)

    await _apply_items(plan, result, one)


async def _apply_saved_views(plan: SectionPlan, result: SectionResult, user: dict, _bg: BackgroundTasks) -> None:
    from app.api.saved_views import create_saved_view, update_saved_view
    from app.models import SavedViewCreate, SavedViewUpdate

    async def one(item: PlanItem) -> None:
        d = item.data
        fields = {"name": d.get("name"), "query_params": d.get("query_params"), "is_pinned": bool(d.get("is_pinned"))}
        if item.action == "add":
            await create_saved_view(SavedViewCreate(**fields), user=user)
        else:
            await update_saved_view(item.existing_id, SavedViewUpdate(**fields), user=user)

    await _apply_items(plan, result, one)


async def _apply_schedules(plan: SectionPlan, result: SectionResult, user: dict, _bg: BackgroundTasks) -> None:
    from app.api.alerts import update_maintenance_schedules
    from app.services.maintenance_service import load_schedules

    current: list[MaintenanceSchedule] = await run_db_query(load_schedules)
    by_name = {s.name.strip().lower(): i for i, s in enumerate(current)}
    merged = list(current)

    async def one(item: PlanItem) -> None:
        d = {k: v for k, v in item.data.items() if k not in ("id", "is_active", "next_run")}
        key = d.get("name", "").strip().lower()
        index = by_name.get(key)
        schedule_id = merged[index].id if index is not None else f"sched_{int(time.time() * 1000)}_{pysecrets.token_hex(3)}"
        schedule = MaintenanceSchedule(**d, id=schedule_id)
        # The checks a save applies, one schedule at a time, so a bad one fails alone
        MaintenanceSchedulesUpdateRequest(schedules=[schedule])
        if index is None:
            by_name[key] = len(merged)
            merged.append(schedule)
        else:
            merged[index] = schedule

    await _apply_items(plan, result, one)
    if result.added or result.replaced:
        try:
            await update_maintenance_schedules(MaintenanceSchedulesUpdateRequest(schedules=merged), user=user)
        except _ITEM_ERRORS as exc:
            # One save for every schedule: a schedule already stored that today's checks refuse stops it
            result.failed.append(ItemFailure(label="Maintenance schedules", error=f"Nothing saved: {_error_text(exc)}"))
            result.added = result.replaced = 0


async def _apply_tokens(plan: SectionPlan, result: SectionResult, user: dict, _bg: BackgroundTasks) -> None:
    from app.api.ingest import refresh_ingest_token
    from app.api.mcp import on_token_updated
    from app.core.api_tokens import validate_token_scopes

    async def one(item: PlanItem) -> None:
        d = item.data
        scopes = validate_token_scopes(d.get("scopes") if isinstance(d.get("scopes"), list) else [])
        name = d["name"].strip()[:100]
        expires_at = _blank(d.get("expires_at"))

        def write(conn) -> int:
            if item.action == "add":
                cur = conn.execute(
                    "INSERT INTO api_tokens (name, token_hash, token_prefix, scopes, created_at, expires_at, created_by)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        name,
                        d["token_hash"],
                        _blank(d.get("token_prefix")) or "ls_live_...",
                        json.dumps(scopes),
                        _blank(d.get("created_at")) or datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        expires_at,
                        _blank(d.get("created_by")) or "admin",
                    ),
                )
                conn.commit()
                return int(cur.lastrowid)
            conn.execute(
                "UPDATE api_tokens SET name = ?, scopes = ?, expires_at = ? WHERE id = ?",
                (name, json.dumps(scopes), expires_at, item.existing_id),
            )
            conn.commit()
            return int(item.existing_id)

        token_id = await run_db_query(write)
        if item.action == "replace":
            refresh_ingest_token(token_id)
            on_token_updated(token_id, scopes)

    await _apply_items(plan, result, one)


def _settings_request(cfg: ConfigFile, channels: dict[str, int], result: SectionResult) -> Optional[SettingsUpdateRequest]:
    wanted = cfg.sections.get("settings")
    wanted = wanted if isinstance(wanted, dict) else {}
    fields: dict[str, Any] = {}
    plain = (
        "ai_enabled", "ai_provider", "ai_system_prompt", "internal_log_level", "check_for_updates",
        "backup_before_migration", "ai_timeout", "ai_thinking_level", "app_url",
        "allow_private_notification_targets", "trusted_proxies", "trust_docker_proxies", "cookie_secure",
        "syslog_max_tcp_connections", "syslog_tcp_inactivity_timeout", "enable_mcp",
        "daily_digest_enabled", "daily_digest_schedule_time",
    )
    for key in plain:
        if key in wanted:
            fields[key] = wanted[key]
    if isinstance(wanted.get("retention_days"), int):
        max_days = get_max_retention_days()
        days = max(1, min(wanted["retention_days"], max_days))
        if days != wanted["retention_days"]:
            result.notes.append(f"Retention set to {days} days: this server allows 1 to {max_days} (MAX_RETENTION_DAYS).")
        fields["retention_days"] = days
    if "daily_digest_target" in wanted:
        target = _blank(wanted["daily_digest_target"])
        channel_id = channels.get(target.strip().lower()) if target else None
        if target and channel_id is None:
            result.notes.append(f"The daily digest's target '{target}' is not on this server: it goes to every enabled target.")
        fields["daily_digest_channel_id"] = channel_id

    providers: dict[str, dict[str, Any]] = {}
    file_providers = wanted.get("ai_providers") if isinstance(wanted.get("ai_providers"), dict) else {}
    for provider, values in file_providers.items():
        pid = normalize_provider_id(provider)
        if pid in AI_PROVIDERS and isinstance(values, dict):
            providers[pid] = {k: values[k] for k in ("ai_model", "ai_fallback_models", "ai_base_url") if k in values}
    keys = (cfg.secrets or {}).get("ai_api_keys")
    for provider, key in (keys.items() if isinstance(keys, dict) else []):
        pid = normalize_provider_id(provider)
        if pid in AI_PROVIDERS and isinstance(key, str):
            providers.setdefault(pid, {})["ai_api_key"] = key
    if providers:
        fields["ai_providers_config"] = {p: AiProviderConfig(**v) for p, v in providers.items()}
    if not fields:
        return None
    return SettingsUpdateRequest(**fields)


async def _apply_settings(cfg: ConfigFile, plan: SectionPlan, result: SectionResult, user: dict, bg: BackgroundTasks) -> None:
    from app.api.settings import update_settings

    try:
        channels = await run_db_query(_channel_ids)
        req = _settings_request(cfg, channels, result)
        if req is None:
            return
        saved = await update_settings(req, background_tasks=bg, user=user)
        for provider in saved.ai_api_key_cleared_providers:
            result.notes.append(
                f"The {provider} API key was cleared because the file changes its base URL. Enter the key again if the new endpoint needs one."
            )
    except _ITEM_ERRORS as exc:
        result.failed.append(ItemFailure(label="Settings", error=_error_text(exc)))
        return
    result.replaced = sum(1 for item in plan.items if item.action == "replace")


_APPLIERS = {
    "notification_targets": _apply_targets,
    "docker_hosts": _apply_docker_hosts,
    "host_aliases": _apply_aliases,
    "alert_rules": _apply_alert_rules,
    "drop_rules": _apply_drop_rules,
    "saved_views": _apply_saved_views,
    "maintenance_schedules": _apply_schedules,
    "api_tokens": _apply_tokens,
}

# One import at a time: two at once would both add an item the other is adding
_import_lock = asyncio.Lock()


@router.post("/import", response_model=ConfigImportResponse)
async def import_configuration(
    req: ConfigImportApplyRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
) -> ConfigImportResponse:
    """
    Applies the ticked sections of a file, in the order of SECTIONS. Each item goes through the
    route that changes it from the web UI; an item that fails is reported and the rest go on.
    """
    unknown = [s for s in req.sections if s not in SECTIONS]
    if unknown:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"Unknown sections: {', '.join(unknown)}")
    cfg = await _read(req)
    if _import_lock.locked():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Another configuration import is running.")
    async with _import_lock:
        results: list[SectionResult] = []
        for name in SECTIONS:
            if name not in req.sections:
                continue
            # Planned again before each section: an earlier one (targets) changes what later ones match
            plans = await run_db_query(lambda conn: plan_import(conn, cfg))
            plan = next((p for p in plans if p.name == name), None)
            if plan is None:
                continue
            result = SectionResult(name=name, notes=list(plan.notes))
            if name == "settings":
                await _apply_settings(cfg, plan, result, user, background_tasks)
            else:
                await _APPLIERS[name](plan, result, user, background_tasks)
            results.append(result)
    logger.info(
        "Configuration imported: %s.",
        ", ".join(f"{r.name} +{r.added} ~{r.replaced} !{len(r.failed)}" for r in results) or "nothing",
    )
    return ConfigImportResponse(sections=results)
