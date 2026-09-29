"""
Daily Digest Service and Background Worker for LogShed.

Performs a 24-hour analytical rollup:
- Total logs ingested
- Error count by top 3 container/host (severity <= 3)
- Top 3 noisy services
- System storage delta

Records history to alert_history and dispatches notifications
to configured target channels.
"""

import asyncio
import datetime
import logging
from pathlib import Path
import sqlite3
from typing import Any, Optional, Union

from app.api.deps import run_db_query
from app.core.config import get_cached_setting, get_db_path
from app.core.utils import parse_iso_to_epoch
from app.services.notifier import get_notifier
from app.services.retention import get_db_footprint_bytes

logger = logging.getLogger(__name__)


def format_bytes(num_bytes: Union[int, float]) -> str:
    """Format byte count into a clean, human-readable string."""
    sign = "-" if num_bytes < 0 else ""
    abs_b = float(abs(num_bytes))
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs_b < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{sign}{int(abs_b)} {unit}"
            return f"{sign}{abs_b:.1f} {unit}"
        abs_b /= 1024.0
    return f"{num_bytes} B"


def compute_daily_digest_rollup(
    conn: sqlite3.Connection,
    window_hours: int = 24,
    db_path: Optional[Union[str, Path]] = None,
) -> dict[str, Any]:
    """
    Query database to compute the 24-hour analytical rollup metrics:
    - total_logs: total log entries ingested in window
    - total_errors: total logs with severity <= 3 in window
    - top_errors: top 3 container/host by error count
    - top_services: top 3 noisy services by log count
    - storage_delta_bytes & storage_delta_str: database footprint delta
    - sample_logs: formatted lines for optional AI synthesis
    """
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    since_dt = now_utc - datetime.timedelta(hours=window_hours)
    since_iso = since_dt.isoformat()

    cursor = conn.cursor()

    # 1. Total logs ingested
    cursor.execute("SELECT COUNT(*) FROM logs WHERE timestamp >= ?", (since_iso,))
    total_logs = cursor.fetchone()[0]

    # 2. Total errors (severity <= 3)
    cursor.execute("SELECT COUNT(*) FROM logs WHERE severity <= 3 AND timestamp >= ?", (since_iso,))
    total_errors = cursor.fetchone()[0]

    # Error count by top 3 container/host (severity <= 3: emerg, alert, crit, error)
    cursor.execute(
        """
        SELECT
            CASE
                WHEN lower(source_alias) = 'docker' THEN app_name
                ELSE COALESCE(NULLIF(source_alias, ''), source_ip, 'unknown')
            END AS entity,
            COUNT(*) as error_count,
            SUM(CASE WHEN severity = 0 THEN 1 ELSE 0 END) as emerg_count,
            SUM(CASE WHEN severity = 1 THEN 1 ELSE 0 END) as alert_count,
            SUM(CASE WHEN severity = 2 THEN 1 ELSE 0 END) as crit_count,
            SUM(CASE WHEN severity = 3 THEN 1 ELSE 0 END) as err_count
        FROM logs
        WHERE severity <= 3 AND timestamp >= ?
        GROUP BY entity
        ORDER BY error_count DESC, entity ASC
        LIMIT 3
        """,
        (since_iso,),
    )
    top_errors = [
        {
            "entity": row[0],
            "error_count": row[1],
            "emerg_count": row[2] or 0,
            "alert_count": row[3] or 0,
            "crit_count": row[4] or 0,
            "err_count": row[5] or 0,
        }
        for row in cursor.fetchall()
    ]

    # 3. Top 3 noisy services
    cursor.execute(
        """
        SELECT
            COALESCE(NULLIF(app_name, ''), 'unknown') AS service,
            COUNT(*) as log_count
        FROM logs
        WHERE timestamp >= ?
        GROUP BY service
        ORDER BY log_count DESC, service ASC
        LIMIT 3
        """,
        (since_iso,),
    )
    top_services = [
        {"service": row[0], "log_count": row[1]}
        for row in cursor.fetchall()
    ]

    # 4. Storage delta
    effective_db = Path(db_path) if db_path else get_db_path()
    current_db_bytes = get_db_footprint_bytes(effective_db)

    cursor.execute(
        """
        SELECT db_size_bytes, disk_free_bytes, recorded_at
        FROM storage_metrics
        WHERE recorded_at >= ?
        ORDER BY recorded_at ASC
        LIMIT 1
        """,
        (since_iso,),
    )
    past_metric = cursor.fetchone()
    if not past_metric:
        cursor.execute(
            """
            SELECT db_size_bytes, disk_free_bytes, recorded_at
            FROM storage_metrics
            ORDER BY recorded_at DESC
            LIMIT 1
            """
        )
        past_metric = cursor.fetchone()

    if past_metric:
        past_db_bytes = past_metric[0] if isinstance(past_metric, tuple) else past_metric["db_size_bytes"]
        delta_bytes = current_db_bytes - past_db_bytes
    else:
        delta_bytes = 0

    if delta_bytes > 0:
        storage_delta_str = f"+{format_bytes(delta_bytes)}"
    elif delta_bytes < 0:
        storage_delta_str = format_bytes(delta_bytes)
    else:
        storage_delta_str = "0 B"

    # Sample logs for AI synthesis (prioritize errors, then recent logs)
    cursor.execute(
        """
        SELECT timestamp, source_alias, source_ip, app_name, severity, message
        FROM logs
        WHERE timestamp >= ?
        ORDER BY (CASE WHEN severity <= 3 THEN 0 ELSE 1 END) ASC, timestamp DESC
        LIMIT 100
        """,
        (since_iso,),
    )
    raw_sample_rows = cursor.fetchall()
    sample_lines = []
    for r in raw_sample_rows:
        src = r[1] or r[2] or "unknown"
        app = r[3] or "unknown"
        sev = r[4]
        msg = r[5] or ""
        sample_lines.append(f"[{r[0]}] [{src}] [{app}] [severity {sev}] {msg}")

    return {
        "since_iso": since_iso,
        "now_iso": now_utc.isoformat(),
        "total_logs": total_logs,
        "total_errors": total_errors,
        "top_errors": top_errors,
        "top_services": top_services,
        "current_db_bytes": current_db_bytes,
        "delta_bytes": delta_bytes,
        "storage_delta_str": storage_delta_str,
        "sample_lines": sample_lines,
    }


def format_digest_body(
    total_logs: int,
    storage_delta_str: str,
    top_errors: list[dict[str, Any]],
    top_services: list[dict[str, Any]],
    app_url: str = "",
) -> str:
    """Format markdown body for notification push and alert history storage."""
    error_lines = []
    if top_errors:
        for item in top_errors:
            cnt = item['error_count']
            crit_sum = (item.get("emerg_count", 0) or 0) + (item.get("alert_count", 0) or 0) + (item.get("crit_count", 0) or 0)
            if crit_sum > 0:
                parts = []
                if item.get("emerg_count"):
                    parts.append(f"{item['emerg_count']:,} emerg")
                if item.get("alert_count"):
                    parts.append(f"{item['alert_count']:,} alert")
                if item.get("crit_count"):
                    parts.append(f"{item['crit_count']:,} crit")
                if item.get("err_count"):
                    parts.append(f"{item['err_count']:,} err")
                breakdown = f" ({', '.join(parts)})" if parts else ""
                error_lines.append(f"- **{item['entity']}**: {cnt:,} event(s){breakdown}")
            else:
                error_lines.append(f"- **{item['entity']}**: {cnt:,} error(s)")
    else:
        error_lines.append("- No errors or critical events recorded in the last 24 hours.")

    service_lines = []
    if top_services:
        for item in top_services:
            service_lines.append(f"- **{item['service']}**: {item['log_count']:,} log(s)")
    else:
        service_lines.append("- No log activity recorded.")

    body_parts = [
        "### 24-Hour Analytical Rollup",
        f"- **Total Logs Ingested:** {total_logs:,}",
        f"- **System Storage Delta:** {storage_delta_str}",
        "",
        "### Top Containers / Hosts by Error & Critical Events (Severity <= 3)",
        "\n".join(error_lines),
        "",
        "### Top Noisy Services",
        "\n".join(service_lines),
    ]

    if app_url and app_url.strip():
        clean_url = app_url.strip().rstrip("/")
        body_parts.extend([
            "",
            f"**Link:** {clean_url}/alerts/history",
        ])

    return "\n".join(body_parts)



async def run_daily_digest(
    db_path: Optional[Union[str, Path]] = None,
    force: bool = False,
    target_channel_id: Optional[int] = None,
) -> dict[str, Any]:
    """
    Execute 24-hour daily digest rollup, alert_history recording, and notification dispatch.
    """
    effective_db = Path(db_path) if db_path else get_db_path()

    def _read_config_and_channels(conn: sqlite3.Connection):
        cur = conn.cursor()
        cur.execute("SELECT key, value FROM system_settings WHERE key IN ('daily_digest_enabled', 'daily_digest_channel_id')")
        settings_map = {row[0]: row[1] for row in cur.fetchall()}

        cur.execute("SELECT id, name, is_enabled FROM notification_channels WHERE is_enabled = 1")
        active_channels = [{"id": r[0], "name": r[1]} for r in cur.fetchall()]

        return settings_map, active_channels

    settings_map, active_channels = await run_db_query(_read_config_and_channels, custom_db_path=effective_db)

    is_enabled_raw = settings_map.get("daily_digest_enabled", "0")
    daily_digest_enabled = is_enabled_raw.strip().lower() in ("1", "true", "yes", "on")

    if not force and not daily_digest_enabled:
        return {"status": "skipped", "reason": "daily_digest_disabled"}

    if not active_channels:
        if force:
            from fastapi import HTTPException, status
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No active notification targets configured.",
            )
        logger.debug("Daily digest skipped: no active notification channels found.")
        return {"status": "skipped", "reason": "no_active_channels"}

    # Resolve target channel: parameter override > configured setting > None (all enabled channels)
    effective_channel_id = target_channel_id
    if effective_channel_id is None:
        configured_channel = settings_map.get("daily_digest_channel_id")
        if configured_channel and configured_channel.strip():
            try:
                effective_channel_id = int(configured_channel.strip())
            except ValueError:
                effective_channel_id = None

    # Compute analytical rollup
    def _compute(conn: sqlite3.Connection):
        return compute_daily_digest_rollup(conn, window_hours=24, db_path=effective_db)

    rollup = await run_db_query(_compute, custom_db_path=effective_db)

    # Format notification body and title
    app_url = str(get_cached_setting("app_url", "")).strip().rstrip("/")
    digest_body = format_digest_body(
        total_logs=rollup["total_logs"],
        storage_delta_str=rollup["storage_delta_str"],
        top_errors=rollup["top_errors"],
        top_services=rollup["top_services"],
        app_url=app_url,
    )
    notification_title = "LogShed: Daily Digest"

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    now_iso = now_utc.isoformat()

    # Sample log snippet for table preview
    sample_preview = None
    if rollup["top_errors"]:
        top_err = rollup["top_errors"][0]
        sample_preview = f"Top error entity: {top_err['entity']} ({top_err['error_count']} errors)"
    elif rollup["top_services"]:
        top_svc = rollup["top_services"][0]
        sample_preview = f"Top service: {top_svc['service']} ({top_svc['log_count']} logs)"

    # Record in alert_history table
    def _insert_history(conn: sqlite3.Connection) -> int:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO alert_history
            (rule_id, rule_name, channel_id, trigger_count, sample_log, incident_summary, ai_enrichment, ai_model, ai_audit_id, triggered_at)
            VALUES (NULL, 'Daily Digest', ?, ?, ?, ?, 0, NULL, NULL, ?)
            """,
            (
                effective_channel_id,
                rollup["total_logs"],
                sample_preview,
                digest_body,
                now_iso,
            ),
        )
        saved_history_id = cur.lastrowid
        # Update daily_digest_last_run in system_settings
        cur.execute(
            """
            INSERT INTO system_settings (key, value, updated_at, is_encrypted)
            VALUES ('daily_digest_last_run', ?, ?, 0)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (now_iso, now_iso),
        )
        conn.commit()
        return saved_history_id

    history_id = await run_db_query(_insert_history, custom_db_path=effective_db)

    # Dispatch notification push
    notifier = get_notifier()
    send_success = False
    try:
        send_success = await notifier.send_notification(
            title=notification_title,
            body=digest_body,
            channel_id=effective_channel_id,
        )
    except Exception as notify_err:
        logger.error(f"Failed to dispatch daily digest notification: {notify_err}")

    logger.info(
        f"Daily digest completed: total_logs={rollup['total_logs']}, "
        f"errors={rollup['total_errors']}, history_id={history_id}, "
        f"notification_sent={send_success}"
    )

    return {
        "status": "ok",
        "history_id": history_id,
        "total_logs": rollup["total_logs"],
        "error_count": rollup["total_errors"],
        "top_errors": rollup["top_errors"],
        "top_services": rollup["top_services"],
        "storage_delta": rollup["storage_delta_str"],
        "channel_id": effective_channel_id,
        "notification_sent": send_success,
        "triggered_at": now_iso,
    }


class DailyDigestWorker:
    """
    Background worker that runs the 24-hour daily digest analytical rollup.
    Evaluates periodically whether 24 hours have elapsed since the last run
    when daily digest is enabled and active notification targets exist.
    """

    def __init__(self, db_path: Union[str, Path]):
        self._db_path = Path(db_path)
        self._running = False
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._manual_trigger = asyncio.Event()

    def trigger(self) -> None:
        """Trigger an immediate daily digest run."""
        self._manual_trigger.set()
        self._wake_event.set()

    async def run(self) -> None:
        """Main loop: check hourly or on trigger for daily digest execution."""
        self._running = True
        logger.info("DailyDigestWorker started.")
        backoff = 5.0

        while self._running:
            try:
                # Check if manual trigger was signaled or 24 hours elapsed
                triggered_manually = self._manual_trigger.is_set()
                if triggered_manually:
                    self._manual_trigger.clear()

                should_run = triggered_manually
                if not should_run:
                    should_run = await asyncio.to_thread(self._should_run_scheduled)

                if should_run:
                    await run_daily_digest(self._db_path, force=triggered_manually)
                    backoff = 5.0

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"DailyDigestWorker error, backing off for {backoff}s: {e}")
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
                    break
                except asyncio.TimeoutError:
                    backoff = min(backoff * 2.0, 3600.0)
                    continue

            if not self._running:
                break

            # Check every 30 seconds or wake on stop/manual trigger
            self._wake_event.clear()
            try:
                await asyncio.wait_for(self._wake_event.wait(), timeout=30.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    def _should_run_scheduled(self) -> bool:
        """
        Synchronously check if daily digest is enabled, targets exist,
        and the configured local schedule time has arrived today without having run yet.
        """
        try:
            conn = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True, timeout=5.0)
        except Exception:
            try:
                conn = sqlite3.connect(self._db_path, timeout=5.0)
            except Exception:
                return False

        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT key, value FROM system_settings WHERE key IN ('daily_digest_enabled', 'daily_digest_last_run', 'daily_digest_schedule_time')"
            )
            rows = {r[0]: r[1] for r in cur.fetchall()}

            is_enabled = rows.get("daily_digest_enabled", "0").strip().lower() in ("1", "true", "yes", "on")
            if not is_enabled:
                return False

            cur.execute("SELECT COUNT(*) FROM notification_channels WHERE is_enabled = 1")
            active_count = cur.fetchone()[0]
            if active_count == 0:
                return False

            now_local = datetime.datetime.now().astimezone()
            today_date_str = now_local.strftime("%Y-%m-%d")

            sched_time_str = rows.get("daily_digest_schedule_time", "09:00") or "09:00"
            try:
                parts = sched_time_str.strip().split(":")
                target_hour = int(parts[0])
                target_minute = int(parts[1]) if len(parts) > 1 else 0
            except (ValueError, IndexError):
                target_hour = 9
                target_minute = 0

            target_time = datetime.time(target_hour, target_minute)
            if now_local.time() < target_time:
                return False

            last_run_str = rows.get("daily_digest_last_run")
            if not last_run_str:
                return True

            try:
                last_run_dt = datetime.datetime.fromisoformat(last_run_str).astimezone()
                last_run_date_str = last_run_dt.strftime("%Y-%m-%d")
                return last_run_date_str != today_date_str
            except Exception:
                last_run_epoch = parse_iso_to_epoch(last_run_str, fallback=0.0)
                now_epoch = now_local.timestamp()
                return (now_epoch - last_run_epoch) >= 86400.0

        except Exception as e:
            logger.warning(f"Error checking daily digest schedule condition: {e}")
            return False
        finally:
            conn.close()

    async def stop(self) -> None:
        """Signal graceful shutdown."""
        self._running = False
        self._stop_event.set()
        self._wake_event.set()
        logger.info("DailyDigestWorker stopping.")
