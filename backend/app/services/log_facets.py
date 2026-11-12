"""
The host and app lists of the Console filters, REST v1 and MCP (``list_services``): distinct sources and
apps across every stored log, configured host aliases and Docker hosts, and their mappings.
"""

import sqlite3
from typing import Any

from app.api.deps import run_db_query
from app.core.utils import AliasIndex, load_former_docker_aliases
from app.models import LogFacetsResponse

# Facet lists (and each mapping value) are capped: syslog is unauthenticated, so random
# APP-NAME or host values could otherwise grow the facets response without limit.
FACET_LIMIT = 500
# Recursive skip-scans stop after this many rows (the extra rows detect truncation).
_FACET_SCAN_LIMIT = FACET_LIMIT + 2
# Upper bound on distinct (source, app) pairs read for the host and app mappings.
_FACET_PAIR_LIMIT = 20 * FACET_LIMIT


def fetch_facets(conn: sqlite3.Connection) -> dict[str, Any]:
    """The facets of every stored log and configured host (get_facets), as a LogFacetsResponse dict."""
    cursor = conn.cursor()
    # Fast loose index skip-scans (O(K log N) B-Tree seeks) over covering index.
    # Eliminates the 7-day cutoff so that any host or app within retention (even with only 1 log)
    # is retained, executing in single-digit milliseconds without scanning millions of rows.

    # 1. Distinct sources
    cursor.execute(
        f"""
        WITH RECURSIVE distinct_sources AS (
            SELECT MIN(source_alias) AS val FROM logs WHERE source_alias != ''
            UNION ALL
            SELECT (SELECT MIN(source_alias) FROM logs WHERE source_alias > s.val AND source_alias != '')
            FROM distinct_sources s
            WHERE s.val IS NOT NULL
            LIMIT {_FACET_SCAN_LIMIT}
        )
        SELECT s.val, (SELECT source_ip FROM logs WHERE source_alias = s.val LIMIT 1)
        FROM distinct_sources s
        WHERE s.val IS NOT NULL;
        """
    )
    sources = cursor.fetchall()

    # 2. Distinct fallback IPs for logs where source_alias is empty
    cursor.execute(
        f"""
        WITH RECURSIVE distinct_ips AS (
            SELECT MIN(source_ip) AS val FROM logs WHERE source_alias = '' AND source_ip != ''
            UNION ALL
            SELECT (SELECT MIN(source_ip) FROM logs WHERE source_alias = '' AND source_ip > s.val AND source_ip != '')
            FROM distinct_ips s
            WHERE s.val IS NOT NULL
            LIMIT {_FACET_SCAN_LIMIT}
        )
        SELECT val FROM distinct_ips WHERE val IS NOT NULL;
        """
    )
    fallback_ips = [r[0] for r in cursor.fetchall()]

    # 3. Distinct apps
    cursor.execute(
        f"""
        WITH RECURSIVE distinct_apps AS (
            SELECT MIN(app_name) AS val FROM logs WHERE app_name != ''
            UNION ALL
            SELECT (SELECT MIN(app_name) FROM logs WHERE app_name > a.val AND app_name != '')
            FROM distinct_apps a
            WHERE a.val IS NOT NULL
            LIMIT {_FACET_SCAN_LIMIT}
        )
        SELECT val FROM distinct_apps WHERE val IS NOT NULL;
        """
    )
    apps_res = [r[0] for r in cursor.fetchall()]

    # 4. Distinct (source_alias, app_name, source_ip) pairs via covering index skip-scan
    cursor.execute(
        f"""
        WITH RECURSIVE cte(s, a, ip) AS (
            SELECT s.source_alias, s.app_name, s.source_ip
            FROM (
                SELECT source_alias, app_name, source_ip
                FROM logs
                WHERE source_alias != '' AND app_name != ''
                ORDER BY source_alias ASC, app_name ASC
                LIMIT 1
            ) s
            UNION ALL
            SELECT nxt.source_alias, nxt.app_name, nxt.source_ip
            FROM cte
            JOIN logs nxt ON nxt.id = COALESCE(
                (SELECT id FROM logs WHERE source_alias = cte.s AND app_name > cte.a AND app_name != '' ORDER BY app_name ASC LIMIT 1),
                (SELECT id FROM logs WHERE source_alias > cte.s AND source_alias != '' AND app_name != '' ORDER BY source_alias ASC, app_name ASC LIMIT 1)
            )
            LIMIT {_FACET_PAIR_LIMIT}
        )
        SELECT s AS source_alias, a AS app_name, ip AS source_ip FROM cte;
        """
    )
    pairs = cursor.fetchall()
    truncated = (
        len(sources) > FACET_LIMIT
        or len(fallback_ips) > FACET_LIMIT
        or len(apps_res) > FACET_LIMIT
        or len(pairs) >= _FACET_PAIR_LIMIT
    )

    # Edge case: Mappings for fallback IPs where source_alias was empty. One skip-scan over
    # idx_logs_source_app_ip lists every (app, IP) pair of alias-less logs with one index seek
    # per pair, instead of one query per IP that read all of that IP's logs.
    if fallback_ips:
        cursor.execute(
            f"""
            WITH RECURSIVE ip_apps(a, ip) AS (
                SELECT * FROM (
                    SELECT app_name, source_ip FROM logs
                    WHERE source_alias = '' AND app_name != '' AND source_ip != ''
                    ORDER BY source_alias, app_name, source_ip
                    LIMIT 1
                )
                UNION ALL
                SELECT nxt.app_name, nxt.source_ip
                FROM ip_apps
                JOIN logs nxt ON nxt.id = COALESCE(
                    (SELECT id FROM logs WHERE source_alias = '' AND app_name = ip_apps.a AND source_ip > ip_apps.ip
                     ORDER BY source_alias, app_name, source_ip LIMIT 1),
                    (SELECT id FROM logs WHERE source_alias = '' AND app_name > ip_apps.a AND source_ip != ''
                     ORDER BY source_alias, app_name, source_ip LIMIT 1)
                )
                LIMIT {_FACET_PAIR_LIMIT}
            )
            SELECT a, ip FROM ip_apps;
            """
        )
        ip_app_rows = cursor.fetchall()
        if len(ip_app_rows) >= _FACET_PAIR_LIMIT:
            truncated = True
        listed_ips = set(fallback_ips)
        apps_by_ip: dict[str, list[str]] = {}
        for app_name, ip in ip_app_rows:
            if ip in listed_ips:
                apps_by_ip.setdefault(ip, []).append(app_name)
        for ip in fallback_ips:
            ip_apps = apps_by_ip.get(ip, [])
            if len(ip_apps) > FACET_LIMIT:
                truncated = True
            for app_name in ip_apps[:FACET_LIMIT + 1]:
                pairs.append((ip, app_name, ip))

    alias_index = AliasIndex.load(conn)
    aliases_map = alias_index.alias_by_ip

    sources_set = set()
    apps_set = set(apps_res)
    host_to_apps: dict[str, set[str]] = {}
    app_to_hosts: dict[str, set[str]] = {}

    # The aliases Docker hosts use now. Only these are listed as hosts without any logs, so a
    # renamed host's old name (or "docker") stays listed only while logs still carry it.
    configured_docker_aliases = {
        str(r[0]).strip()
        for r in cursor.execute("SELECT source_alias FROM docker_endpoints").fetchall()
        if r[0] and str(r[0]).strip()
    }

    # Add all configured Syslog aliases
    for alias in aliases_map.values():
        if alias:
            sources_set.add(alias)
            if alias not in host_to_apps:
                host_to_apps[alias] = set()

    # Add all configured Docker aliases
    for d_alias in configured_docker_aliases:
        if d_alias:
            sources_set.add(d_alias)
            if d_alias not in host_to_apps:
                host_to_apps[d_alias] = set()

    # Add all distinct sources from logs (even if they logged without app_name)
    for row in sources:
        s = row[0]
        ip = row[1]
        if alias_index.is_docker(s, ip):
            canonical = s or "docker"
        else:
            canonical = (ip and aliases_map.get(ip)) or aliases_map.get(s) or s or ip
        sources_set.add(canonical)
        if canonical not in host_to_apps:
            host_to_apps[canonical] = set()

    for ip in fallback_ips:
        canonical = aliases_map.get(ip, ip)
        sources_set.add(canonical)
        if canonical not in host_to_apps:
            host_to_apps[canonical] = set()

    for r in pairs:
        raw_alias = r[0]
        app = r[1]
        ip = r[2]

        # Canonical host resolution:
        # Docker logs preserve their Docker endpoint alias without resolving to Syslog IP aliases.
        if alias_index.is_docker(raw_alias, ip):
            canonical_host = raw_alias or "docker"
        else:
            canonical_host = aliases_map.get(ip) or aliases_map.get(raw_alias) or raw_alias or ip
        if not canonical_host:
            continue

        sources_set.add(canonical_host)

        if app:
            apps_set.add(app)
            if canonical_host not in host_to_apps:
                host_to_apps[canonical_host] = set()
            host_to_apps[canonical_host].add(app)

            if app not in app_to_hosts:
                app_to_hosts[app] = set()
            app_to_hosts[app].add(canonical_host)

    # Safety: Ensure no IP that has a distinct alias remains in sources_set or host_to_apps
    for ip, alias in aliases_map.items():
        if ip != alias:
            if ip in sources_set:
                sources_set.remove(ip)
            if ip in host_to_apps:
                if alias in host_to_apps:
                    host_to_apps[alias].update(host_to_apps[ip])
                else:
                    host_to_apps[alias] = set(host_to_apps[ip])
                del host_to_apps[ip]
            for app, hosts in app_to_hosts.items():
                if ip in hosts:
                    hosts.remove(ip)
                    hosts.add(alias)

    def _capped(values) -> list[str]:
        nonlocal truncated
        ordered = sorted(values)
        if len(ordered) > FACET_LIMIT:
            truncated = True
            ordered = ordered[:FACET_LIMIT]
        return ordered

    sources_out = _capped(sources_set)
    apps_out = _capped(apps_set)
    kept_sources = set(sources_out)
    kept_apps = set(apps_out)
    mapping = {
        h: _capped(apps) for h, apps in sorted(host_to_apps.items()) if h in kept_sources
    }
    if len(mapping) < len(host_to_apps):
        truncated = True
    app_mapping = {
        a: _capped(hosts) for a, hosts in sorted(app_to_hosts.items()) if a in kept_apps
    }
    if len(app_mapping) < len(app_to_hosts):
        truncated = True
    return {
        "sources": sources_out,
        "apps": apps_out,
        "host_to_apps": mapping,
        "source_app_mapping": mapping,
        "app_to_hosts": app_mapping,
        "truncated": truncated,
        "limit": FACET_LIMIT,
        "former_docker_aliases": load_former_docker_aliases(conn),
    }


async def get_facets() -> LogFacetsResponse:
    """
    Fetch distinct sources, apps, and their bidirectional mappings
    across the entire database, including configured host aliases.
    Each list (and each mapping value) holds at most FACET_LIMIT entries in alphabetical
    order; `truncated` is true when anything was left out. Syslog is unauthenticated, so
    random APP-NAME or host values must not grow the response without limit.
    """
    return LogFacetsResponse(**await run_db_query(fetch_facets))
