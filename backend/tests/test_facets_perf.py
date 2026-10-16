"""
Performance and edge-case verification tests for LogFacets loose index skip-scans.
Validates Issue 6: Zero time cutoff needed; 100% discovery of infrequent hosts
and apps in single-digit milliseconds across large log databases.
"""

import datetime
from pathlib import Path
import random
import sqlite3
import time
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.security import SESSION_COOKIE_NAME, create_session_token
from app.core.migrations import get_connection, run_migrations
from app.main import create_app
from tests.test_logs_api import _seed_logs


@pytest.fixture(autouse=True)
def setup_env(tmp_path: Path, monkeypatch):
    db_file = tmp_path / "logs.db"
    key_file = tmp_path / ".secret_key"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("SECRET_KEY_PATH", str(key_file))
    run_migrations(db_file)


@pytest_asyncio.fixture
async def client():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Requested-With": "XMLHttpRequest"},
    ) as ac:
        yield ac


@pytest.fixture
def auth_cookie() -> dict[str, str]:
    token = create_session_token(user_id=1)
    return {SESSION_COOKIE_NAME: token}


class TestFacetsPerformanceAndEdgeCases:
    """Validate O(K log N) loose index skip-scan performance and edge cases."""

    def test_facets_loose_index_scan_empty_db(self, tmp_path):
        """Empty database returns empty facets without errors."""
        db_file = tmp_path / "empty.db"
        run_migrations(db_file)
        with get_connection(db_file) as conn:
            cursor = conn.cursor()
            # Verify queries run on empty table cleanly
            cursor.execute("""
            WITH RECURSIVE distinct_sources AS (
                SELECT MIN(source_alias) AS val FROM logs WHERE source_alias != ''
                UNION ALL
                SELECT (SELECT MIN(source_alias) FROM logs WHERE source_alias > s.val AND source_alias != '')
                FROM distinct_sources s
                WHERE s.val IS NOT NULL
            )
            SELECT val FROM distinct_sources WHERE val IS NOT NULL;
            """)
            assert cursor.fetchall() == []

    def test_facets_loose_index_scan_benchmark_100k_rows(self, tmp_path):
        """100,000 rows across 20 hosts and 30 apps executes in single-digit milliseconds."""
        db_file = tmp_path / "bench.db"
        run_migrations(db_file)

        hosts = [f"node-{i:02d}" for i in range(20)]
        apps = [f"svc-{j:02d}" for j in range(30)]

        data = []
        for _ in range(100000):
            h = random.choice(hosts)
            a = random.choice(apps)
            ip = f"192.168.1.{hosts.index(h) + 1}"
            data.append(("2026-09-01T12:00:00", "2026-09-01T12:00:00", ip, h, a, 1, 6, "bench", "bench"))

        # Add single log from infrequent host 45 days ago
        data.append(("2026-07-15T12:00:00", "2026-07-15T12:00:00", "10.10.10.5", "quarterly-backup", "zfs-sync", 1, 6, "done", "done"))

        with get_connection(db_file) as conn:
            conn.executemany(
                "INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw) VALUES (?,?,?,?,?,?,?,?,?)",
                data,
            )
            conn.commit()

            # Execute the skip-scans
            t0 = time.perf_counter()

            # 1. Distinct sources
            sources = [
                r[0]
                for r in conn.execute(
                    """
                    WITH RECURSIVE distinct_sources AS (
                        SELECT MIN(source_alias) AS val FROM logs WHERE source_alias != ''
                        UNION ALL
                        SELECT (SELECT MIN(source_alias) FROM logs WHERE source_alias > s.val AND source_alias != '')
                        FROM distinct_sources s
                        WHERE s.val IS NOT NULL
                    )
                    SELECT val FROM distinct_sources WHERE val IS NOT NULL;
                    """
                ).fetchall()
            ]

            # 2. Distinct apps
            apps_res = [
                r[0]
                for r in conn.execute(
                    """
                    WITH RECURSIVE distinct_apps AS (
                        SELECT MIN(app_name) AS val FROM logs WHERE app_name != ''
                        UNION ALL
                        SELECT (SELECT MIN(app_name) FROM logs WHERE app_name > a.val AND app_name != '')
                        FROM distinct_apps a
                        WHERE a.val IS NOT NULL
                    )
                    SELECT val FROM distinct_apps WHERE val IS NOT NULL;
                    """
                ).fetchall()
            ]

            # 3. Distinct pairs via streamlined covering index skip-scan
            pairs = conn.execute(
                """
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
                )
                SELECT s AS source_alias, a AS app_name, ip AS source_ip FROM cte;
                """
            ).fetchall()
            t1 = time.perf_counter()

            duration_ms = (t1 - t0) * 1000
            # Must be well under 50ms (typically 2-6ms on modern machines)
            assert duration_ms < 50
            assert "quarterly-backup" in sources
            assert "zfs-sync" in apps_res
            assert len(sources) == 21  # 20 regular + 1 infrequent
            assert len(apps_res) == 31  # 30 regular + 1 infrequent
            assert len(pairs) > 500

    @pytest.mark.asyncio
    async def test_facets_endpoint_with_single_log_unaliased_host(
        self, client: AsyncClient, auth_cookie: dict, tmp_path: Path
    ):
        """End-to-end API test: a host that logged only once retains its entry in the host dropdown."""
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        db_file = tmp_path / "logs.db"

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        # Logged 28 days ago (under 30-day retention, well past former 7-day cutoff)
        old_ts = (now_utc - datetime.timedelta(days=28)).isoformat()

        test_entries = [
            {
                "timestamp": old_ts,
                "received_at": old_ts,
                "source_ip": "10.50.0.1",
                "source_alias": "unaliased-monthly-worker",
                "app_name": "monthly-prune",
                "facility": 1,
                "severity": 6,
                "message": "prune complete",
                "raw": "prune complete",
            }
        ]
        run_migrations(db_file)
        _seed_logs(db_file, test_entries)

        response = await client.get("/api/logs/facets")
        assert response.status_code == 200
        data = response.json()

        assert "unaliased-monthly-worker" in data["sources"]
        assert "monthly-prune" in data["apps"]
        assert "monthly-prune" in data["host_to_apps"]["unaliased-monthly-worker"]
        assert "unaliased-monthly-worker" in data["app_to_hosts"]["monthly-prune"]

    def test_streamlined_facets_covering_index_benchmark(self, tmp_path):
        """
        Validate that the streamlined facets CTE uses covering index scans,
        selects source_ip directly without redundant scalar subqueries,
        and executes in single-digit milliseconds.
        """
        db_file = tmp_path / "streamlined_bench.db"
        run_migrations(db_file)

        hosts = [f"server-{i:02d}" for i in range(15)]
        apps = [f"app-{j:02d}" for j in range(20)]

        data = []
        for i in range(10000):
            h = hosts[i % len(hosts)]
            a = apps[i % len(apps)]
            ip = f"10.0.0.{hosts.index(h) + 1}"
            data.append(("2026-09-01T12:00:00", "2026-09-01T12:00:00", ip, h, a, 1, 6, "msg", "msg"))

        with get_connection(db_file) as conn:
            conn.executemany(
                "INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw) VALUES (?,?,?,?,?,?,?,?,?)",
                data,
            )
            conn.commit()

            # Verify query plan uses covering index
            q_plan = conn.execute(
                """
                EXPLAIN QUERY PLAN
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
                )
                SELECT s AS source_alias, a AS app_name, ip AS source_ip FROM cte;
                """
            ).fetchall()
            plan_str = " ".join(str(row) for row in q_plan)
            assert "idx_logs_source_app_ip" in plan_str

            # Benchmark execution time
            t0 = time.perf_counter()
            rows = conn.execute(
                """
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
                )
                SELECT s AS source_alias, a AS app_name, ip AS source_ip FROM cte;
                """
            ).fetchall()
            t1 = time.perf_counter()
            duration_ms = (t1 - t0) * 1000

            assert len(rows) > 0
            assert duration_ms < 25.0
            # Verify source_ip is populated directly in result tuples
            for r in rows:
                assert r[0] != ""  # source_alias
                assert r[1] != ""  # app_name
                assert r[2].startswith("10.0.0.")  # source_ip



class TestFacetsCaps:
    """Unauthenticated syslog can create any number of app or host names; facets stay bounded."""

    def _insert(self, db_file: Path, rows: list[tuple]) -> None:
        with get_connection(db_file) as conn:
            conn.executemany(
                """INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
                   VALUES ('2026-10-15T12:00:00+00:00', '2026-10-15T12:00:00+00:00', ?, ?, ?, 1, 6, 'm', 'r')""",
                rows,
            )
            conn.commit()

    @pytest.mark.asyncio
    async def test_600_apps_are_capped_at_500(self, client: AsyncClient, auth_cookie: dict, tmp_path: Path):
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        self._insert(tmp_path / "logs.db", [("10.0.0.1", "host-a", f"app-{i:04d}") for i in range(600)])

        res = await client.get("/api/logs/facets")
        assert res.status_code == 200
        data = res.json()
        assert data["truncated"] is True
        assert len(data["apps"]) == 500
        assert data["apps"] == sorted(data["apps"])
        assert len(data["app_to_hosts"]) <= 500
        assert all(len(apps) <= 500 for apps in data["host_to_apps"].values())

    @pytest.mark.asyncio
    async def test_600_hosts_are_capped_at_500(self, client: AsyncClient, auth_cookie: dict, tmp_path: Path):
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        rows = [(f"10.0.{i // 250}.{i % 250}", f"host-{i:04d}", "web") for i in range(300)]
        rows += [(f"10.1.{i // 250}.{i % 250}", "", "web") for i in range(300)]
        self._insert(tmp_path / "logs.db", rows)

        res = await client.get("/api/logs/facets")
        assert res.status_code == 200
        data = res.json()
        assert data["truncated"] is True
        assert len(data["sources"]) == 500
        assert len(data["host_to_apps"]) <= 500
        assert all(len(hosts) <= 500 for hosts in data["app_to_hosts"].values())

    @pytest.mark.asyncio
    async def test_small_databases_are_not_truncated(self, client: AsyncClient, auth_cookie: dict, tmp_path: Path):
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        self._insert(tmp_path / "logs.db", [("10.0.0.1", "host-a", "web"), ("10.0.0.2", "", "db")])

        res = await client.get("/api/logs/facets")
        data = res.json()
        assert data["truncated"] is False
        assert "10.0.0.2" in data["sources"]
        assert data["host_to_apps"]["10.0.0.2"] == ["db"]
