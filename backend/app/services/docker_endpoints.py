"""
Reading Docker host rows (the docker_endpoints table) for the Docker hosts API and the collector pool.
"""

import sqlite3
from typing import Any, Optional

_ENDPOINT_COLUMNS = (
    "id, name, url, source_alias, enabled, tls_enabled, verify_hostname, "
    "ca_cert, client_cert, client_key, exclude_containers, created_at, updated_at"
)


def load_docker_endpoints(
    conn: sqlite3.Connection,
    *,
    enabled_only: bool = False,
    endpoint_id: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Docker host rows in id order, as stored (the client key stays encrypted)."""
    clauses = []
    params: list[Any] = []
    if enabled_only:
        clauses.append("enabled = 1")
    if endpoint_id is not None:
        clauses.append("id = ?")
        params.append(endpoint_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    cur = conn.execute(f"SELECT {_ENDPOINT_COLUMNS} FROM docker_endpoints {where} ORDER BY id ASC", params)
    columns = [d[0] for d in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def public_endpoint(row: dict[str, Any]) -> dict[str, Any]:
    """The fields the Docker hosts API shows for a row: flags as booleans, no client key, not connected."""
    return {
        "id": row["id"],
        "name": row["name"],
        "url": row["url"],
        "source_alias": row["source_alias"],
        "enabled": bool(row["enabled"]),
        "tls_enabled": bool(row["tls_enabled"]),
        "ca_cert": row["ca_cert"],
        "client_cert": row["client_cert"],
        "has_client_key": bool(row["client_key"] and str(row["client_key"]).strip()),
        "verify_hostname": bool(row["verify_hostname"]),
        "exclude_containers": row["exclude_containers"] or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "is_connected": False,
        "active_containers": 0,
        "last_error": None,
        "last_activity": None,
        "latency_ms": None,
    }
