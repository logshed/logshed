"""
Host aliases API endpoints for IP to Hostname mapping management.
"""

import asyncio
import datetime
import logging
import time
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

logger = logging.getLogger(__name__)

from app.api.deps import get_current_user, run_db_query
from app.collectors.syslog import reload_active_alias_caches
from app.core.utils import load_docker_source_aliases
from app.models import HostAliasCreate, HostAliasResponse, MessageResponse

router = APIRouter(prefix="/aliases", tags=["Host Aliases"])

_ALIAS_CLASH_HINT = "Choose a different name so syslog and container logs from the same machine stay separate."


def raise_if_alias_used_by_docker(conn, alias: str) -> None:
    """409 when a syslog host alias would match a Docker host alias (letter case ignored)."""
    folded = alias.strip().lower()
    if any(a.lower() == folded for a in load_docker_source_aliases(conn)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"The alias '{alias.strip()}' is already used by a Docker host. {_ALIAS_CLASH_HINT}",
        )


def raise_if_alias_used_by_syslog(conn, alias: str) -> None:
    """409 when a Docker host alias would match a syslog host alias (letter case ignored)."""
    row = conn.execute(
        "SELECT 1 FROM host_aliases WHERE ip != 'docker' AND lower(alias) = lower(?) LIMIT 1",
        (alias.strip(),),
    ).fetchone()
    if row:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"The alias '{alias.strip()}' is already used by a syslog host alias. {_ALIAS_CLASH_HINT}",
        )


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


def keyset_alias_backfill(
    conn,
    source_ip: str,
    alias_filter_sql: str,
    alias_filter_params: tuple,
    target_alias: str,
    batch_size: int = 1000,
) -> int:
    """
    Set source_alias = target_alias on rows with this source_ip that match alias_filter_sql,
    newest first, in batches of batch_size committed separately.

    Each batch starts below the lowest id of the previous one (keyset batching), so rows
    already updated are never walked again. alias_filter_sql must write the column as
    `+source_alias`: the unary plus keeps SQLite on the (source_ip, rowid) index, so each
    batch is a range seek instead of a scan of every remaining row for that alias.
    Returns the number of rows updated.
    """
    cursor = conn.cursor()
    where_sql = f"source_ip = ? AND ({alias_filter_sql})"
    where_params = (source_ip, *alias_filter_params)
    upper_id: Optional[int] = None
    total = 0
    while True:
        bound_sql = "" if upper_id is None else " AND id < ?"
        bound_params: tuple = () if upper_id is None else (upper_id,)
        cursor.execute(
            f"""
            SELECT MIN(id), COUNT(*) FROM (
                SELECT id FROM logs WHERE {where_sql}{bound_sql} ORDER BY id DESC LIMIT ?
            )
            """,
            (*where_params, *bound_params, batch_size),
        )
        low_id, count = cursor.fetchone()
        if not count:
            break
        cursor.execute(
            f"UPDATE logs SET source_alias = ? WHERE {where_sql} AND id >= ?{bound_sql}",
            (target_alias, *where_params, low_id, *bound_params),
        )
        total += max(cursor.rowcount, 0)
        conn.commit()
        if count < batch_size:
            break
        upper_id = low_id
        time.sleep(0.005)
    return total


def _batch_update_log_aliases(conn, source_ip: str, target_alias: str, batch_size: int = 1000) -> None:
    """
    Retroactively update or revert source_alias for existing logs in chunked batches.
    Prevents long table locks on large datasets while interleaving concurrent writes.
    Protects Docker logs from being clobbered by Syslog host IP alias updates.
    """
    if source_ip == "docker":
        keyset_alias_backfill(
            conn,
            "docker",
            "+source_alias != ? OR +source_alias IS NULL",
            (target_alias,),
            target_alias,
            batch_size,
        )
    else:
        docker_aliases = sorted(load_docker_source_aliases(conn))
        placeholders = ",".join("?" for _ in docker_aliases)
        keyset_alias_backfill(
            conn,
            source_ip,
            f"+source_alias IS NULL OR (+source_alias != ? AND +source_alias NOT IN ({placeholders}))",
            (target_alias, *docker_aliases),
            target_alias,
            batch_size,
        )


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
        if clean_ip != "docker":
            cursor.execute("SELECT alias FROM host_aliases WHERE ip = ?", (clean_ip,))
            current = cursor.fetchone()
            if not current or current[0] != clean_alias:
                raise_if_alias_used_by_docker(conn, clean_alias)
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
