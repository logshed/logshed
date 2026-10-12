"""
Host aliases API endpoints for IP to Hostname mapping management.
"""

import asyncio
import datetime
import logging
import time
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

logger = logging.getLogger(__name__)

from app.api.deps import get_current_user, run_db_query
from app.collectors.syslog import reload_active_alias_caches
from app.models import HostAliasCreate, HostAliasResponse, MessageResponse

router = APIRouter(prefix="/aliases", tags=["Host Aliases"])


@router.get("", response_model=list[HostAliasResponse])
async def list_aliases(user: dict = Depends(get_current_user)) -> list[HostAliasResponse]:
    """List all registered IP-to-hostname alias mappings."""
    def _get_all(conn):
        cursor = conn.cursor()
        cursor.execute("SELECT ip, alias, notes, created_at FROM host_aliases ORDER BY ip ASC")
        rows = cursor.fetchall()
        return [
            HostAliasResponse(
                ip=r["ip"],
                alias=r["alias"],
                notes=r["notes"],
                created_at=str(r["created_at"]),
            )
            for r in rows
        ]

    return await run_db_query(_get_all)


def _batch_update_log_aliases(conn, source_ip: str, target_alias: str, batch_size: int = 1000) -> None:
    """
    Retroactively update or revert source_alias for existing logs in chunked batches.
    Prevents long table locks on large datasets while interleaving concurrent writes.
    Protects Docker logs from being clobbered by Syslog host IP alias updates.
    """
    cursor = conn.cursor()
    if source_ip == "docker":
        update_query = """
            UPDATE logs SET source_alias = ?
            WHERE source_ip = 'docker' AND (source_alias != ? OR source_alias IS NULL) AND id IN (
                SELECT id FROM logs
                WHERE source_ip = 'docker' AND (source_alias != ? OR source_alias IS NULL)
                ORDER BY id DESC
                LIMIT ?
            )
        """
        params = (target_alias, target_alias, target_alias, batch_size)
    else:
        docker_aliases: list[str] = []
        try:
            cur = conn.cursor()
            cur.execute("SELECT source_alias FROM docker_endpoints WHERE source_alias IS NOT NULL")
            docker_aliases = [r[0].strip() for r in cur.fetchall() if r[0] and str(r[0]).strip()]
        except Exception:
            docker_aliases = []

        try:
            cur = conn.cursor()
            cur.execute("SELECT value FROM system_settings WHERE key = 'docker_source_alias'")
            row = cur.fetchone()
            if row and row[0] and str(row[0]).strip():
                docker_aliases.append(str(row[0]).strip())
        except Exception:
            pass

        docker_aliases = list(set(docker_aliases) | {"docker"})
        placeholders = ",".join("?" for _ in docker_aliases)
        update_query = f"""
            UPDATE logs SET source_alias = ?
            WHERE source_ip = ?
              AND (source_alias IS NULL OR source_alias NOT IN ({placeholders}))
              AND id IN (
                SELECT id FROM logs
                WHERE source_ip = ?
                  AND (source_alias IS NULL OR (source_alias != ? AND source_alias NOT IN ({placeholders})))
                ORDER BY id DESC
                LIMIT ?
            )
        """
        params = (target_alias, source_ip, *docker_aliases, source_ip, target_alias, *docker_aliases, batch_size)

    while True:
        cursor.execute(update_query, params)
        count = cursor.rowcount
        conn.commit()
        if count < batch_size:
            break
        time.sleep(0.005)


@router.post("", response_model=HostAliasResponse)
async def create_or_update_alias(
    req: HostAliasCreate,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
) -> HostAliasResponse:
    """Create or update an IP-to-hostname alias mapping, updating existing logs retroactively."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    clean_ip = req.ip.strip()
    clean_alias = req.alias.strip()

    def _upsert(conn):
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO host_aliases (ip, alias, notes, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(ip) DO UPDATE SET
                alias = excluded.alias,
                notes = excluded.notes
            """,
            (clean_ip, clean_alias, req.notes, now),
        )
        conn.commit()

        cursor.execute("SELECT ip, alias, notes, created_at FROM host_aliases WHERE ip = ?", (clean_ip,))
        row = cursor.fetchone()
        return HostAliasResponse(
            ip=row["ip"],
            alias=row["alias"],
            notes=row["notes"],
            created_at=str(row["created_at"]),
        )

    res = await run_db_query(_upsert)
    await asyncio.to_thread(reload_active_alias_caches)

    def _run_batch(conn):
        try:
            _batch_update_log_aliases(conn, clean_ip, clean_alias, batch_size=1000)
        except Exception as exc:
            logger.error(f"Background retroactive alias update failed for {clean_ip} -> {clean_alias}: {exc}")

    background_tasks.add_task(run_db_query, _run_batch)
    return res


@router.delete("/{ip}", response_model=MessageResponse)
async def delete_alias(
    ip: str,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
) -> MessageResponse:
    """Delete a host alias mapping by IP address, reverting existing logs to raw IP."""
    def _delete(conn):
        cursor = conn.cursor()
        cursor.execute("DELETE FROM host_aliases WHERE ip = ?", (ip,))
        deleted = cursor.rowcount > 0
        conn.commit()
        return deleted

    deleted = await run_db_query(_delete)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Host alias for IP '{ip}' not found.",
        )

    await asyncio.to_thread(reload_active_alias_caches)

    def _run_batch_delete(conn):
        try:
            _batch_update_log_aliases(conn, ip, ip, batch_size=1000)
        except Exception as exc:
            logger.error(f"Background retroactive alias reversion failed for {ip}: {exc}")

    background_tasks.add_task(run_db_query, _run_batch_delete)
    return MessageResponse(status="ok")
