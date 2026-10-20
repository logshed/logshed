"""
Docker Collector Pool for multi-host container log ingestion.

Supervises multiple concurrent DockerTailer workers connecting to local and remote
Docker daemons (UDS, TCP socket proxies, and mTLS endpoints).
"""

import asyncio
import logging
import sqlite3
from pathlib import Path
from typing import Any, Optional, Union

from app.collectors.docker_collector import DockerTailer, create_docker_ssl_context, env_docker_disabled
from app.collectors.syslog import AliasCache
from app.core.config import get_cached_setting, get_db_path
from app.core.migrations import get_connection
from app.core.pipeline import KeyedMultilineAssembler
from app.core.security import decrypt_value

logger = logging.getLogger(__name__)

TLS_HOSTNAME_HINT = (
    "The certificate does not list this host name or IP address. If the daemon is reached by an IP "
    "address its certificate does not list, turn off 'Verify TLS host name' for this host."
)


def is_tls_hostname_mismatch(text: Optional[str]) -> bool:
    """True when an error message reports a certificate host name or IP address mismatch."""
    lowered = (text or "").lower()
    return "hostname mismatch" in lowered or "ip address mismatch" in lowered


def with_tls_hostname_hint(error: Optional[str]) -> Optional[str]:
    """Append the verify host name hint to a TLS host name mismatch error."""
    if error and is_tls_hostname_mismatch(error) and TLS_HOSTNAME_HINT not in error:
        return f"{error} {TLS_HOSTNAME_HINT}"
    return error


async def _supervise_tailer(tailer: DockerTailer) -> None:
    """
    Supervisor loop running a DockerTailer instance with exception isolation
    and exponential backoff restart.
    """
    backoff = 1.0
    while True:
        try:
            await tailer.run()
            break
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error(
                f"Docker tailer '{tailer.name}' (id={tailer.endpoint_id}) crashed: {exc}. Restarting in {backoff:.1f}s..."
            )
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                break
            backoff = min(backoff * 2.0, 60.0)


class DockerCollectorPool:
    """
    Multi-instance pool managing DockerTailer instances across multiple endpoints.
    """

    def __init__(
        self,
        assembler: KeyedMultilineAssembler,
        db_path: Optional[Union[str, Path]] = None,
        alias_cache: Optional[AliasCache] = None,
    ):
        self._assembler = assembler
        self._db_path = Path(db_path) if db_path else get_db_path()
        self._alias_cache = alias_cache
        self._tailers: dict[int, DockerTailer] = {}
        self._tasks: dict[int, asyncio.Task] = {}
        self._running: bool = False
        # enable_docker is the switch of the local host (id 1) only; other hosts follow their own row.
        self._primary_enabled: bool = bool(get_cached_setting("enable_docker", True))
        self._lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def primary_enabled(self) -> bool:
        return self._primary_enabled

    def _endpoint_enabled(self, row: dict[str, Any]) -> bool:
        """Whether an endpoint should collect: its own flag, and enable_docker and ENABLE_DOCKER for host 1."""
        enabled = bool(row.get("enabled", 1))
        if int(row["id"]) == 1:
            enabled = enabled and self._primary_enabled and not env_docker_disabled()
        return enabled

    def get_tailer(self, endpoint_id: int = 1) -> Optional[DockerTailer]:
        """Retrieve a specific DockerTailer instance by endpoint ID."""
        return self._tailers.get(endpoint_id)

    def get_tailers(self) -> dict[int, DockerTailer]:
        """Retrieve all active DockerTailer instances."""
        return dict(self._tailers)

    def _build_tailer(self, row: dict[str, Any]) -> DockerTailer:
        """Construct a DockerTailer instance from endpoint configuration."""
        endpoint_id = int(row["id"])
        name = str(row["name"])
        url = str(row["url"])
        source_alias = str(row.get("source_alias") or "docker")
        enabled = self._endpoint_enabled(row)
        exclude_containers = str(row.get("exclude_containers") or "")
        tls_enabled = bool(row.get("tls_enabled", 0))

        ssl_ctx = None
        if tls_enabled:
            client_key = None
            raw_key = row.get("client_key")
            if raw_key and raw_key.strip():
                try:
                    client_key = decrypt_value(raw_key.strip())
                except Exception as e:
                    logger.warning(f"Failed to decrypt TLS client key for Docker host '{name}': {e}")
                    client_key = None

            ssl_ctx = create_docker_ssl_context(
                ca_cert=row.get("ca_cert"),
                client_cert=row.get("client_cert"),
                client_key=client_key,
                verify_hostname=bool(row.get("verify_hostname", 1)),
            )

        return DockerTailer(
            assembler=self._assembler,
            db_path=self._db_path,
            alias_cache=self._alias_cache,
            endpoint_id=endpoint_id,
            name=name,
            url=url,
            source_alias=source_alias,
            enable_docker=enabled,
            exclude_containers=exclude_containers,
            ssl_context=ssl_ctx,
        )

    async def start(self) -> None:
        """Query enabled Docker endpoints from SQLite and start supervised tailers."""
        async with self._lock:
            if self._running:
                return
            self._running = True

            def _query_endpoints(conn: sqlite3.Connection) -> list[dict[str, Any]]:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT id, name, url, source_alias, enabled, tls_enabled,
                           ca_cert, client_cert, client_key, exclude_containers, verify_hostname
                    FROM docker_endpoints
                    WHERE enabled = 1
                    ORDER BY id ASC
                    """
                )
                rows = cur.fetchall()
                results = []
                for r in rows:
                    results.append({
                        "id": r["id"],
                        "name": r["name"],
                        "url": r["url"],
                        "source_alias": r["source_alias"],
                        "enabled": r["enabled"],
                        "tls_enabled": r["tls_enabled"],
                        "ca_cert": r["ca_cert"],
                        "client_cert": r["client_cert"],
                        "client_key": r["client_key"],
                        "exclude_containers": r["exclude_containers"],
                        "verify_hostname": r["verify_hostname"],
                    })
                return results

            from app.api.deps import run_db_query
            endpoints = await run_db_query(_query_endpoints, custom_db_path=self._db_path)

            for ep in endpoints:
                eid = ep["id"]
                try:
                    tailer = self._build_tailer(ep)
                    self._tailers[eid] = tailer
                    task = asyncio.create_task(
                        _supervise_tailer(tailer),
                        name=f"docker-collector-{eid}",
                    )
                    self._tasks[eid] = task
                    logger.info(f"Started Docker tailer '{tailer.name}' (id={eid})")
                except Exception as exc:
                    logger.error(f"Failed to start Docker tailer '{ep.get('name')}' (id={eid}): {exc}")

    async def stop(self) -> None:
        """Halt all running tailers and drain background supervisor tasks."""
        async with self._lock:
            self._running = False
            for eid, tailer in list(self._tailers.items()):
                try:
                    await tailer.stop()
                except Exception as e:
                    logger.warning(f"Error stopping Docker tailer id={eid}: {e}")

            for eid, task in list(self._tasks.items()):
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

            self._tailers.clear()
            self._tasks.clear()
            logger.info("Docker collector pool stopped.")

    async def add_endpoint(self, endpoint_data: dict[str, Any]) -> None:
        """Dynamically add and start a new Docker endpoint."""
        async with self._lock:
            eid = int(endpoint_data["id"])
            if eid in self._tailers:
                await self._stop_endpoint_internal(eid)

            if self._endpoint_enabled(endpoint_data) and self._running:
                try:
                    tailer = self._build_tailer(endpoint_data)
                    self._tailers[eid] = tailer
                    task = asyncio.create_task(
                        _supervise_tailer(tailer),
                        name=f"docker-collector-{eid}",
                    )
                    self._tasks[eid] = task
                    logger.info(f"Dynamically added Docker tailer '{tailer.name}' (id={eid})")
                except Exception as exc:
                    logger.error(f"Failed to add Docker tailer (id={eid}): {exc}")

    async def _stop_endpoint_internal(self, endpoint_id: int) -> None:
        """Helper to cancel and remove an endpoint worker without acquiring self._lock."""
        tailer = self._tailers.pop(endpoint_id, None)
        task = self._tasks.pop(endpoint_id, None)

        if tailer:
            try:
                await tailer.stop()
            except Exception as e:
                logger.warning(f"Error stopping tailer {endpoint_id}: {e}")

        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def remove_endpoint(self, endpoint_id: int) -> None:
        """Dynamically stop and remove a Docker endpoint."""
        async with self._lock:
            await self._stop_endpoint_internal(endpoint_id)
            logger.info(f"Removed Docker tailer (id={endpoint_id})")

    async def reload_endpoint(self, endpoint_id: int, endpoint_data: dict[str, Any]) -> None:
        """Restart a Docker endpoint worker with updated configuration."""
        async with self._lock:
            await self._stop_endpoint_internal(endpoint_id)
            if self._endpoint_enabled(endpoint_data) and self._running:
                try:
                    tailer = self._build_tailer(endpoint_data)
                    self._tailers[endpoint_id] = tailer
                    task = asyncio.create_task(
                        _supervise_tailer(tailer),
                        name=f"docker-collector-{endpoint_id}",
                    )
                    self._tasks[endpoint_id] = task
                    logger.info(f"Reloaded Docker tailer '{tailer.name}' (id={endpoint_id})")
                except Exception as exc:
                    logger.error(f"Failed to reload Docker tailer (id={endpoint_id}): {exc}")

    async def set_primary_enabled(self, enabled: bool) -> None:
        """Record enable_docker, the switch of the local host (id 1) only.

        Other Docker hosts keep collecting: each follows its own enabled flag. Callers apply
        the change with reload_endpoint(1, ...).
        """
        async with self._lock:
            if self._primary_enabled != enabled:
                self._primary_enabled = enabled
                logger.info(f"Local Docker host (id=1) collection set to: {enabled}")

    async def get_statuses(self) -> list[dict[str, Any]]:
        """Collect live connection statuses and metrics for all configured hosts."""
        def _query_all(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, name, url, source_alias, enabled, tls_enabled,
                       ca_cert, client_cert, client_key, exclude_containers, verify_hostname,
                       created_at, updated_at
                FROM docker_endpoints
                ORDER BY id ASC
                """
            )
            rows = cur.fetchall()
            results = []
            for r in rows:
                results.append({
                    "id": r["id"],
                    "name": r["name"],
                    "url": r["url"],
                    "source_alias": r["source_alias"],
                    "enabled": bool(r["enabled"]),
                    "tls_enabled": bool(r["tls_enabled"]),
                    "ca_cert": r["ca_cert"],
                    "client_cert": r["client_cert"],
                    "has_client_key": bool(r["client_key"] and str(r["client_key"]).strip()),
                    "exclude_containers": r["exclude_containers"] or "",
                    "verify_hostname": bool(r["verify_hostname"]),
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                })
            return results

        from app.api.deps import run_db_query
        endpoints = await run_db_query(_query_all, custom_db_path=self._db_path)

        for ep in endpoints:
            eid = ep["id"]
            tailer = self._tailers.get(eid)
            if tailer:
                status = tailer.get_status()
                ep["is_connected"] = status["is_connected"]
                ep["active_containers"] = status["active_containers"]
                ep["last_error"] = with_tls_hostname_hint(status["last_error"])
                ep["last_activity"] = status["last_activity"]
                ep["latency_ms"] = status["latency_ms"]
            else:
                ep["is_connected"] = False
                ep["active_containers"] = 0
                ep["last_error"] = None
                ep["last_activity"] = None
                ep["latency_ms"] = None

        return endpoints
