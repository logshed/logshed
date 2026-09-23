"""
Host aliases API endpoints for IP to Hostname mapping management.
"""

import asyncio
import datetime
import time
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

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
    """
    cursor = conn.cursor()
    update_query = """
        UPDATE logs SET source_alias = ?
        WHERE source_ip = ? AND id IN (
            SELECT id FROM logs
            WHERE source_ip = ? AND source_alias != ?
            ORDER BY id DESC
            LIMIT ?
        )
    """
    while True:
        cursor.execute(update_query, (target_alias, source_ip, source_ip, target_alias, batch_size))
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
        _batch_update_log_aliases(conn, clean_ip, clean_alias, batch_size=1000)

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
        _batch_update_log_aliases(conn, ip, ip, batch_size=1000)

    background_tasks.add_task(run_db_query, _run_batch_delete)
    return MessageResponse(status="ok")
