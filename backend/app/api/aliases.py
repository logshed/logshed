"""
Host aliases API endpoints for IP to Hostname mapping management.
"""

import asyncio
import datetime
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from app.api.deps import get_current_user, run_db_query
from app.collectors.syslog import reload_active_alias_caches
from app.core.utils import load_docker_source_aliases, load_former_docker_aliases
from app.models import HostAliasCreate, HostAliasResponse, MessageResponse
from app.services.alias_rename import alias_in_use, follow_alias_rename, reload_after_alias_rename
from app.services import log_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/aliases", tags=["Host Aliases"])

_ALIAS_CLASH_HINT = "Choose a different name so syslog and container logs from the same machine stay separate."


def raise_if_alias_used_by_docker(conn, alias: str) -> None:
    """409 when a syslog host alias would match a Docker host alias (letter case ignored)."""
    folded = alias.strip().lower()
    if any(a.lower() == folded for a in load_former_docker_aliases(conn)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"The alias '{alias.strip()}' belonged to a Docker host that was deleted or renamed, "
                f"and its old container logs still carry it. {_ALIAS_CLASH_HINT}"
            ),
        )
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


def _batch_update_log_aliases(conn, source_ip: str, target_alias: str, batch_size: int = 1000) -> None:
    """
    Retroactively update or revert source_alias for existing logs in chunked batches.
    Prevents long table locks on large datasets while interleaving concurrent writes.
    Protects Docker logs from being clobbered by Syslog host IP alias updates.
    """
    if source_ip == "docker":
        log_store.relabel_host(conn, "docker", target_alias, batch_size=batch_size)
    else:
        docker_aliases = tuple(sorted(load_docker_source_aliases(conn)))
        log_store.relabel_host(conn, source_ip, target_alias, keep_aliases=docker_aliases, batch_size=batch_size)


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

    followed: list = [None]

    def _upsert(conn):
        cursor = conn.cursor()
        cursor.execute("SELECT alias FROM host_aliases WHERE ip = ?", (clean_ip,))
        current = cursor.fetchone()
        if clean_ip != "docker":
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
        # Rules and views naming the host by its previous alias follow the rename. A first alias
        # rewrites nothing: rules written against the host's HOSTNAME or IP are left as they are.
        if current and current[0] != clean_alias and not alias_in_use(conn, current[0], except_ip=clean_ip):
            followed[0] = follow_alias_rename(conn, current[0], clean_alias)
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
    await reload_after_alias_rename(followed[0])

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
    followed: list = [None]

    def _delete(conn):
        cursor = conn.cursor()
        cursor.execute("SELECT alias FROM host_aliases WHERE ip = ?", (ip,))
        current = cursor.fetchone()
        cursor.execute("DELETE FROM host_aliases WHERE ip = ?", (ip,))
        deleted = cursor.rowcount > 0
        # The host's logs go back to its IP, so rules and views naming the alias follow to the IP
        if current and not alias_in_use(conn, current[0], except_ip=ip):
            followed[0] = follow_alias_rename(conn, current[0], ip)
        conn.commit()
        return deleted

    deleted = await run_db_query(_delete)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Host alias for IP '{ip}' not found.",
        )

    await asyncio.to_thread(reload_active_alias_caches)
    await reload_after_alias_rename(followed[0])

    def _run_batch_delete(conn):
        try:
            _batch_update_log_aliases(conn, ip, ip, batch_size=1000)
        except Exception as exc:
            logger.error(f"Background retroactive alias reversion failed for {ip}: {exc}")

    background_tasks.add_task(run_db_query, _run_batch_delete)
    return MessageResponse(status="ok")
