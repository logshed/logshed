"""
Keep rules and views pointing at a host when its alias is renamed or deleted.

Stored logs are rewritten to a host's new alias, or back to its IP when a syslog host alias is
deleted (`_batch_update_log_aliases`, `_batch_update_docker_aliases`), and new logs carry that name
from the start. Drop rules, active maintenance sessions and saved views store the name they were
given, so afterwards they would silently stop matching the host. `follow_alias_rename` moves the
ones that name the host by its exact old alias (letter case and surrounding spaces ignored, as the
matching does) to the new name.
Wildcard patterns are left alone, and so is everything when another host still has the old alias,
because the old name still matches that host.
"""

import asyncio
import json
import logging
import sqlite3
from dataclasses import dataclass
from typing import Any, Optional

from app.api.deps import run_db_query
from app.core.rule_index import exact_match_key
from app.services.drop_filter import get_drop_filter
from app.services.maintenance_service import refresh_maintenance_cache

logger = logging.getLogger(__name__)


@dataclass
class AliasRenameResult:
    drop_rules: int = 0
    maintenance_sessions: int = 0
    saved_views: int = 0


def alias_in_use(
    conn: sqlite3.Connection,
    alias: str,
    except_ip: Optional[str] = None,
    except_endpoint_id: Optional[int] = None,
) -> bool:
    """True when a syslog host alias or Docker host alias other than the one named equals alias (any case)."""
    key = exact_match_key(alias)
    if key is None:
        return False
    syslog = conn.execute(
        "SELECT 1 FROM host_aliases WHERE lower(trim(alias)) = ? AND ip IS NOT ? LIMIT 1",
        (key, except_ip),
    ).fetchone()
    if syslog:
        return True
    docker = conn.execute(
        "SELECT 1 FROM docker_endpoints WHERE lower(trim(source_alias)) = ? AND id IS NOT ? LIMIT 1",
        (key, except_endpoint_id),
    ).fetchone()
    return docker is not None


def _rename_in_list(values: list[Any], old_key: str, new_alias: str) -> Optional[list[Any]]:
    """values with the old alias replaced and duplicates removed, or None when it was not there."""
    if not any(isinstance(v, str) and exact_match_key(v) == old_key for v in values):
        return None
    renamed: list[Any] = []
    for v in values:
        if isinstance(v, str) and exact_match_key(v) == old_key:
            v = new_alias
        if v not in renamed:
            renamed.append(v)
    return renamed


def _rename_in_view(params: dict[str, Any], old_key: str, new_alias: str) -> bool:
    """Rename the host in a saved view's sources list and source field (comma separated). True when changed."""
    changed = False
    sources = params.get("sources")
    if isinstance(sources, list):
        renamed = _rename_in_list(sources, old_key, new_alias)
        if renamed is not None:
            params["sources"] = renamed
            changed = True
    source = params.get("source")
    if isinstance(source, str):
        renamed = _rename_in_list(source.split(","), old_key, new_alias)
        if renamed is not None:
            params["source"] = ",".join(renamed)
            changed = True
    return changed


def follow_alias_rename(conn: sqlite3.Connection, old_alias: str, new_alias: str) -> AliasRenameResult:
    """
    Point drop rules, active maintenance sessions and saved views that name old_alias exactly at
    new_alias, inside the caller's transaction (the caller commits, then calls
    reload_after_alias_rename). The caller checks first that no other host still has old_alias.
    """
    result = AliasRenameResult()
    old_key = exact_match_key(old_alias)
    new_alias = new_alias.strip()
    if old_key is None or not new_alias or old_alias.strip() == new_alias:
        return result

    rule_ids = [
        r[0]
        for r in conn.execute("SELECT id, source_pattern FROM drop_rules WHERE source_pattern IS NOT NULL")
        if exact_match_key(r[1]) == old_key
    ]
    conn.executemany("UPDATE drop_rules SET source_pattern = ? WHERE id = ?", [(new_alias, i) for i in rule_ids])
    result.drop_rules = len(rule_ids)

    session_ids = [
        r[0]
        for r in conn.execute(
            "SELECT id, target_host FROM maintenance_sessions WHERE is_active = 1 AND target_host IS NOT NULL"
        )
        if exact_match_key(r[1]) == old_key
    ]
    conn.executemany(
        "UPDATE maintenance_sessions SET target_host = ? WHERE id = ?", [(new_alias, i) for i in session_ids]
    )
    result.maintenance_sessions = len(session_ids)

    view_updates = []
    for view_id, raw in conn.execute("SELECT id, query_params FROM saved_views"):
        try:
            params = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(params, dict) and _rename_in_view(params, old_key, new_alias):
            view_updates.append((json.dumps(params), view_id))
    conn.executemany("UPDATE saved_views SET query_params = ? WHERE id = ?", view_updates)
    result.saved_views = len(view_updates)
    return result


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


async def reload_after_alias_rename(result: Optional[AliasRenameResult]) -> None:
    """After the commit: apply the moved rules and sessions to the next log, and log what moved."""
    if result is None:
        return
    if result.drop_rules:
        await asyncio.to_thread(get_drop_filter().reload_rules)
    if result.maintenance_sessions:
        await run_db_query(refresh_maintenance_cache)
    if result.drop_rules or result.maintenance_sessions or result.saved_views:
        logger.info(
            "Host alias changed: %s, %s and %s now use the new name",
            _plural(result.drop_rules, "drop rule"),
            _plural(result.maintenance_sessions, "maintenance session"),
            _plural(result.saved_views, "saved view"),
        )
