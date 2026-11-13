"""
The host and app lists of the Console filters, REST v1 and MCP (``list_services``): distinct sources and
apps across every stored log, configured host aliases and Docker hosts, and their mappings.
"""

import sqlite3
from typing import Any

from app.api.deps import run_db_query
from app.core.utils import AliasIndex, load_former_docker_aliases
from app.models import LogFacetsResponse
from app.services import log_store

# Facet lists (and each mapping value) are capped: syslog is unauthenticated, so random
# APP-NAME or host values could otherwise grow the facets response without limit.
FACET_LIMIT = 500
# Recursive skip-scans stop after this many rows (the extra rows detect truncation).
_FACET_SCAN_LIMIT = FACET_LIMIT + 2
# Upper bound on distinct (source, app) pairs read for the host and app mappings.
_FACET_PAIR_LIMIT = 20 * FACET_LIMIT


def fetch_facets(conn: sqlite3.Connection) -> dict[str, Any]:
    """The facets of every stored log and configured host (get_facets), as a LogFacetsResponse dict."""
    seen = log_store.seen_names(conn, _FACET_SCAN_LIMIT, _FACET_PAIR_LIMIT)
    truncated = (
        len(seen.aliases) > FACET_LIMIT
        or len(seen.bare_ips) > FACET_LIMIT
        or len(seen.apps) > FACET_LIMIT
        or len(seen.alias_apps) >= _FACET_PAIR_LIMIT
        or len(seen.bare_ip_apps) >= _FACET_PAIR_LIMIT
    )
    pairs, bare_ip_truncated = _host_app_pairs(seen)
    alias_index = AliasIndex.load(conn)
    facets = _FacetSets()
    facets.add_configured_hosts(alias_index.alias_by_ip, _configured_docker_aliases(conn))
    facets.add_seen(seen, pairs, alias_index)
    facets.fold_addresses_into_aliases(alias_index.alias_by_ip)
    result = facets.capped()
    result["truncated"] = result["truncated"] or truncated or bare_ip_truncated
    result["former_docker_aliases"] = load_former_docker_aliases(conn)
    return result


def _host_app_pairs(seen: log_store.SeenNames) -> tuple[list[tuple[str, str, str]], bool]:
    """
    Every (host, app, address) pair seen: logs with a host name, then logs with only an address
    (named by it). Also whether an address had more than FACET_LIMIT apps (some left out).
    """
    pairs = list(seen.alias_apps)
    if not seen.bare_ips:
        return pairs, False
    truncated = False
    listed_ips = set(seen.bare_ips)
    apps_by_ip: dict[str, list[str]] = {}
    for app_name, ip in seen.bare_ip_apps:
        if ip in listed_ips:
            apps_by_ip.setdefault(ip, []).append(app_name)
    for ip in seen.bare_ips:
        ip_apps = apps_by_ip.get(ip, [])
        if len(ip_apps) > FACET_LIMIT:
            truncated = True
        for app_name in ip_apps[:FACET_LIMIT + 1]:
            pairs.append((ip, app_name, ip))
    return pairs, truncated


def _configured_docker_aliases(conn: sqlite3.Connection) -> set[str]:
    """
    The aliases Docker hosts use now. Only these are listed as hosts without any logs, so a
    renamed host's old name (or "docker") stays listed only while logs still carry it.
    """
    return {
        str(r[0]).strip()
        for r in conn.execute("SELECT source_alias FROM docker_endpoints").fetchall()
        if r[0] and str(r[0]).strip()
    }


class _FacetSets:
    """The host and app lists and their mappings while they are built, before the caps."""

    def __init__(self) -> None:
        self.sources: set[str] = set()
        self.apps: set[str] = set()
        self.host_to_apps: dict[str, set[str]] = {}
        self.app_to_hosts: dict[str, set[str]] = {}
        self.truncated = False

    def _add_host(self, host: str) -> None:
        self.sources.add(host)
        self.host_to_apps.setdefault(host, set())

    def add_configured_hosts(self, aliases_by_ip: dict[str, str], docker_aliases: set[str]) -> None:
        """List every configured syslog alias and Docker host name, with or without logs."""
        for alias in aliases_by_ip.values():
            if alias:
                self._add_host(alias)
        for d_alias in docker_aliases:
            if d_alias:
                self._add_host(d_alias)

    def add_seen(self, seen: log_store.SeenNames, pairs: list[tuple[str, str, str]], alias_index: AliasIndex) -> None:
        """Add the hosts, addresses and apps stored logs carry, each host under its canonical name."""
        aliases_map = alias_index.alias_by_ip
        self.apps.update(seen.apps)
        # Hosts from logs, even if they logged without app_name
        for alias, ip in seen.aliases:
            self._add_host(_canonical_host(alias, ip, alias_index))
        for ip in seen.bare_ips:
            self._add_host(aliases_map.get(ip, ip))
        for raw_alias, app, ip in pairs:
            canonical_host = _canonical_host(raw_alias, ip, alias_index)
            if not canonical_host:
                continue
            self.sources.add(canonical_host)
            if app:
                self.apps.add(app)
                self.host_to_apps.setdefault(canonical_host, set()).add(app)
                self.app_to_hosts.setdefault(app, set()).add(canonical_host)

    def fold_addresses_into_aliases(self, aliases_map: dict[str, str]) -> None:
        """An address that has an alias is listed only under its alias, with its apps moved there."""
        for ip, alias in aliases_map.items():
            if ip == alias:
                continue
            self.sources.discard(ip)
            if ip in self.host_to_apps:
                self.host_to_apps.setdefault(alias, set()).update(self.host_to_apps.pop(ip))
            for hosts in self.app_to_hosts.values():
                if ip in hosts:
                    hosts.remove(ip)
                    hosts.add(alias)

    def _capped(self, values) -> list[str]:
        ordered = sorted(values)
        if len(ordered) > FACET_LIMIT:
            self.truncated = True
            ordered = ordered[:FACET_LIMIT]
        return ordered

    def capped(self) -> dict[str, Any]:
        """The lists and mappings in alphabetical order, each at most FACET_LIMIT long."""
        sources_out = self._capped(self.sources)
        apps_out = self._capped(self.apps)
        kept_sources = set(sources_out)
        kept_apps = set(apps_out)
        mapping = {
            h: self._capped(apps) for h, apps in sorted(self.host_to_apps.items()) if h in kept_sources
        }
        if len(mapping) < len(self.host_to_apps):
            self.truncated = True
        app_mapping = {
            a: self._capped(hosts) for a, hosts in sorted(self.app_to_hosts.items()) if a in kept_apps
        }
        if len(app_mapping) < len(self.app_to_hosts):
            self.truncated = True
        return {
            "sources": sources_out,
            "apps": apps_out,
            "host_to_apps": mapping,
            "source_app_mapping": mapping,
            "app_to_hosts": app_mapping,
            "truncated": self.truncated,
            "limit": FACET_LIMIT,
        }


def _canonical_host(raw_alias: str, ip: str, alias_index: AliasIndex) -> str:
    """
    The name a log's host is listed under: Docker logs keep their Docker host name ("docker"
    when they have none); syslog logs take the alias configured for their address or name.
    """
    if alias_index.is_docker(raw_alias, ip):
        return raw_alias or "docker"
    aliases_map = alias_index.alias_by_ip
    return (ip and aliases_map.get(ip)) or aliases_map.get(raw_alias) or raw_alias or ip


async def get_facets() -> LogFacetsResponse:
    """
    Fetch distinct sources, apps, and their bidirectional mappings
    across the entire database, including configured host aliases.
    Each list (and each mapping value) holds at most FACET_LIMIT entries in alphabetical
    order; `truncated` is true when anything was left out. Syslog is unauthenticated, so
    random APP-NAME or host values must not grow the response without limit.
    """
    return LogFacetsResponse(**await run_db_query(fetch_facets))
