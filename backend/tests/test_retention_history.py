"""Tests for history retention pruning, AI audit linkage, and the prune preview endpoint."""

import datetime
import sqlite3
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core import pipeline as pipeline_mod
from app.core.api_tokens import _last_used_timestamps, api_token_rate_limiter
from app.core.migrations import get_connection as _raw_get_connection, run_migrations
from app.core.rate_limiter import login_rate_limiter
from app.core.security import (
    SESSION_COOKIE_NAME,
    create_session_token,
    get_or_create_master_key,
    reset_crypto_cache,
)
from app.core.sse import sse_manager
from app.main import create_app
from app.services import maintenance_service as ms
from app.services import retention as retention_mod
from app.services.alert_evaluator import init_alert_evaluator
from app.services.drop_filter import init_drop_filter
from app.services.operation_guard import reset_operation_state
from app.services.retention import compute_prune_preview, execute_prune, get_prune_schedule

NOW = datetime.datetime.now(datetime.timezone.utc)


def _ago(days: float) -> str:
    return (NOW - datetime.timedelta(days=days)).isoformat()


def get_connection(path):
    conn = _raw_get_connection(path)
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture(autouse=True)
def reset_test_env(tmp_path: Path, monkeypatch):
    pipeline_mod._log_queue = None
    login_rate_limiter.reset()
    api_token_rate_limiter.reset()
    reset_crypto_cache()
    sse_manager.reset()
    _last_used_timestamps.clear()
    ms.update_cached_maintenance_sessions([])
    reset_operation_state()
    retention_mod._last_prune_at = None
    retention_mod._next_scheduled_prune_at = None

    db_file = tmp_path / "logs.db"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("SECRET_KEY_PATH", str(tmp_path / ".secret_key"))
    monkeypatch.delenv("LOGSHED_SECRET_KEY", raising=False)
    monkeypatch.delenv("MAX_RETENTION_DAYS", raising=False)

    run_migrations(db_file)
    get_or_create_master_key(tmp_path / ".secret_key")
    init_drop_filter(db_file)
    init_alert_evaluator(db_file)

    yield

    ms.update_cached_maintenance_sessions([])
    pipeline_mod._log_queue = None
    login_rate_limiter.reset()
    api_token_rate_limiter.reset()
    _last_used_timestamps.clear()
    reset_operation_state()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "logs.db"


@pytest_asyncio.fixture
async def client():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"x-requested-with": "XMLHttpRequest"},
    ) as ac:
        yield ac


@pytest.fixture
def auth_headers():
    token = create_session_token(user_id=1)
    return {
        "Cookie": f"{SESSION_COOKIE_NAME}={token}",
        "x-requested-with": "XMLHttpRequest",
    }


def _insert_log(conn, ts: str) -> int:
    cur = conn.execute(
        """
        INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name,
                          facility, severity, message, raw)
        VALUES (?, ?, '10.0.0.1', 'host-a', 'web', 1, 6, 'msg', 'raw')
        """,
        (ts, ts),
    )
    return cur.lastrowid


def _insert_audit(conn, ts: str) -> int:
    cur = conn.execute(
        """
        INSERT INTO ai_audit_log (timestamp, source_alias, app_name, log_count, model,
                                  prompt_sent, response_text)
        VALUES (?, 'host-a', 'web', 1, 'm', 'prompt', 'response')
        """,
        (ts,),
    )
    return cur.lastrowid


def _insert_history(
    conn,
    ts: str,
    rule_name: str = "SSH Rule",
    rule_id=None,
    sample_log="sample",
    ai_audit_id=None,
    session_id=None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO alert_history (rule_id, rule_name, trigger_count, sample_log,
                                   ai_audit_id, triggered_at, maintenance_session_id)
        VALUES (?, ?, 1, ?, ?, ?, ?)
        """,
        (rule_id, rule_name, sample_log, ai_audit_id, ts, session_id),
    )
    return cur.lastrowid


def _history_ids(db_path: Path) -> set[int]:
    with get_connection(db_path) as conn:
        return {r[0] for r in conn.execute("SELECT id FROM alert_history")}


def _audit_ids(db_path: Path) -> set[int]:
    with get_connection(db_path) as conn:
        return {r[0] for r in conn.execute("SELECT id FROM ai_audit_log")}


def _seed_mixed(db_path: Path) -> dict:
    """Old and recent rows of every kind. Returns ids grouped by expected fate."""
    ids: dict = {"delete_history": set(), "keep_history": set(), "delete_audit": set(), "keep_audit": set()}
    with get_connection(db_path) as conn:
        # Alert firings
        ids["delete_history"].add(_insert_history(conn, _ago(30)))
        ids["keep_history"].add(_insert_history(conn, _ago(1)))
        # Daily digests
        ids["delete_history"].add(_insert_history(conn, _ago(30), rule_name="Daily Digest", sample_log=None))
        ids["keep_history"].add(_insert_history(conn, _ago(1), rule_name="Daily Digest", sample_log=None))
        # On-demand analyses with linked audit rows
        old_audit = _insert_audit(conn, _ago(30))
        new_audit = _insert_audit(conn, _ago(1))
        ids["delete_history"].add(
            _insert_history(conn, _ago(30), rule_name="On-Demand Analysis", sample_log=None, ai_audit_id=old_audit)
        )
        ids["keep_history"].add(
            _insert_history(conn, _ago(1), rule_name="On-Demand Analysis", sample_log=None, ai_audit_id=new_audit)
        )
        ids["delete_audit"].add(old_audit)
        ids["keep_audit"].add(new_audit)
        # Orphan audit records (no history row)
        ids["delete_audit"].add(_insert_audit(conn, _ago(30)))
        ids["keep_audit"].add(_insert_audit(conn, _ago(1)))

        # Maintenance windows: ended long ago, ended recently, still running
        old = ms.create_maintenance_session(conn, duration_minutes=30)
        recent = ms.create_maintenance_session(conn, duration_minutes=30)
        running = ms.create_maintenance_session(conn, duration_minutes=30)
        ms.disable_maintenance_sessions(conn, session_id=old["session_id"])
        ms.disable_maintenance_sessions(conn, session_id=recent["session_id"])
        conn.execute(
            "UPDATE maintenance_sessions SET ended_at = ? WHERE session_id = ?",
            (_ago(30), old["session_id"]),
        )
        # The running window started long ago but has not ended, so it must survive
        conn.execute(
            "UPDATE alert_history SET triggered_at = ? WHERE maintenance_session_id = ?",
            (_ago(30), running["session_id"]),
        )
        conn.commit()
        for sid, bucket in (
            (old["session_id"], "delete_history"),
            (recent["session_id"], "keep_history"),
            (running["session_id"], "keep_history"),
        ):
            row = conn.execute(
                "SELECT id FROM alert_history WHERE maintenance_session_id = ?", (sid,)
            ).fetchone()
            ids[bucket].add(row[0])
        ids["sessions"] = (old["session_id"], recent["session_id"], running["session_id"])
    return ids


class TestHistoryPruning:
    def test_prune_applies_retention_to_every_history_kind(self, db_path: Path):
        ids = _seed_mixed(db_path)

        result = execute_prune(db_path, retention_days=14)

        remaining = _history_ids(db_path)
        assert not (remaining & ids["delete_history"])
        assert ids["keep_history"] <= remaining
        assert result["deleted_history"] == len(ids["delete_history"])

    def test_prune_deletes_linked_and_orphan_ai_audit_rows(self, db_path: Path):
        ids = _seed_mixed(db_path)

        result = execute_prune(db_path, retention_days=14)

        remaining = _audit_ids(db_path)
        assert not (remaining & ids["delete_audit"])
        assert ids["keep_audit"] <= remaining
        assert result["deleted_ai_audit"] == len(ids["delete_audit"])

    def test_audit_row_still_referenced_by_a_kept_row_is_kept(self, db_path: Path):
        with get_connection(db_path) as conn:
            shared = _insert_audit(conn, _ago(30))
            _insert_history(conn, _ago(30), ai_audit_id=shared)
            kept = _insert_history(conn, _ago(1), ai_audit_id=shared)
            conn.commit()

        result = execute_prune(db_path, retention_days=14)

        assert result["deleted_history"] == 1
        assert result["deleted_ai_audit"] == 0
        assert shared in _audit_ids(db_path)
        assert kept in _history_ids(db_path)

    def test_old_ended_sessions_are_removed_and_running_sessions_kept(self, db_path: Path):
        ids = _seed_mixed(db_path)
        old, recent, running = ids["sessions"]

        execute_prune(db_path, retention_days=14)

        with get_connection(db_path) as conn:
            present = {r[0] for r in conn.execute("SELECT session_id FROM maintenance_sessions")}
        assert old not in present
        assert recent in present
        assert running in present

    def test_deleted_logs_counts_logs_only(self, db_path: Path):
        _seed_mixed(db_path)
        with get_connection(db_path) as conn:
            for _ in range(3):
                _insert_log(conn, _ago(30))
            _insert_log(conn, _ago(1))
            conn.commit()

        result = execute_prune(db_path, retention_days=14)

        assert result["deleted_logs"] == 3
        assert result["deleted_history"] > 0

    def test_prune_records_last_cleanup_time(self, db_path: Path):
        assert get_prune_schedule()["last_cleanup_at"] is None
        execute_prune(db_path, retention_days=14)
        assert get_prune_schedule()["last_cleanup_at"] is not None


class TestPreview:
    def test_preview_counts_match_what_prune_deletes(self, db_path: Path):
        _seed_mixed(db_path)
        with get_connection(db_path) as conn:
            for _ in range(4):
                _insert_log(conn, _ago(30))
            _insert_log(conn, _ago(1))
            conn.commit()
        # Index the logs so the FTS guard allows deletion, as the prune does
        from app.services.fts_indexer import index_pending_logs

        index_pending_logs(db_path)

        with get_connection(db_path) as conn:
            preview = compute_prune_preview(conn, 14)

        result = execute_prune(db_path, retention_days=14)

        assert preview["logs"] == result["deleted_logs"] == 4
        assert preview["history_total"] == result["deleted_history"]
        assert preview["ai_audit"] == result["deleted_ai_audit"]
        assert preview["total"] == (
            preview["logs"] + preview["history_total"] + preview["metrics"] + preview["ai_audit"]
        )

    def test_preview_splits_history_by_kind(self, db_path: Path):
        _seed_mixed(db_path)
        with get_connection(db_path) as conn:
            preview = compute_prune_preview(conn, 14)
        assert preview["history_alerts"] == 1
        assert preview["history_digests"] == 1
        assert preview["history_on_demand"] == 1
        assert preview["history_maintenance"] == 1
        assert preview["history_total"] == 4

    def test_preview_logs_honour_fts_indexed_guard(self, db_path: Path):
        from app.services.fts_indexer import index_pending_logs

        with get_connection(db_path) as conn:
            _insert_log(conn, _ago(30))
            _insert_log(conn, _ago(30))
            conn.commit()
        index_pending_logs(db_path)
        # A third old log arrives after indexing and must not be counted or deleted yet
        with get_connection(db_path) as conn:
            _insert_log(conn, _ago(30))
            conn.commit()
            preview = compute_prune_preview(conn, 14)
        assert preview["logs"] == 2

        # The prune applies the same guard when it does not index first
        result = execute_prune(db_path, retention_days=14, index_pending=False)
        assert result["deleted_logs"] == 2

    def test_preview_is_read_only(self, db_path: Path):
        ids = _seed_mixed(db_path)
        with get_connection(db_path) as conn:
            compute_prune_preview(conn, 14)
        assert ids["delete_history"] <= _history_ids(db_path)
        assert ids["delete_audit"] <= _audit_ids(db_path)

    @pytest.mark.asyncio
    async def test_preview_endpoint_returns_counts(
        self, client: AsyncClient, auth_headers: dict, db_path: Path
    ):
        _seed_mixed(db_path)
        res = await client.get(
            "/api/maintenance/prune/preview", params={"retention_days": 14}, headers=auth_headers
        )
        assert res.status_code == 200
        body = res.json()
        assert body["retention_days"] == 14
        assert body["history_total"] == 4
        assert body["ai_audit"] == 2
        assert body["total"] >= 6
        assert body["cutoff"]
        assert "next_cleanup_at" in body and "last_cleanup_at" in body

    @pytest.mark.asyncio
    async def test_preview_defaults_to_saved_retention(
        self, client: AsyncClient, auth_headers: dict, db_path: Path
    ):
        with get_connection(db_path) as conn:
            _insert_history(conn, _ago(5))
            conn.execute(
                "INSERT INTO system_settings (key, value, updated_at, is_encrypted) "
                "VALUES ('retention_days', '3', ?, 0) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (NOW.isoformat(),),
            )
            conn.commit()
        res = await client.get("/api/maintenance/prune/preview", headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["retention_days"] == 3
        assert res.json()["history_alerts"] == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("days", [0, -1, 100000])
    async def test_preview_rejects_out_of_range_retention(
        self, client: AsyncClient, auth_headers: dict, days: int
    ):
        res = await client.get(
            "/api/maintenance/prune/preview", params={"retention_days": days}, headers=auth_headers
        )
        assert res.status_code == 422

    @pytest.mark.asyncio
    async def test_preview_requires_auth(self, client: AsyncClient):
        res = await client.get("/api/maintenance/prune/preview", params={"retention_days": 14})
        assert res.status_code == 401

    @pytest.mark.asyncio
    async def test_preview_reports_next_scheduled_cleanup(
        self, client: AsyncClient, auth_headers: dict
    ):
        retention_mod._set_next_scheduled_prune(3600.0)
        res = await client.get(
            "/api/maintenance/prune/preview", params={"retention_days": 14}, headers=auth_headers
        )
        next_at = datetime.datetime.fromisoformat(res.json()["next_cleanup_at"])
        delta = (next_at - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
        assert 3000 < delta <= 3600

        retention_mod._set_next_scheduled_prune(None)
        res = await client.get(
            "/api/maintenance/prune/preview", params={"retention_days": 14}, headers=auth_headers
        )
        assert res.json()["next_cleanup_at"] is None


class TestPruneResponses:
    @pytest.mark.asyncio
    async def test_ui_prune_reports_separate_counts(
        self, client: AsyncClient, auth_headers: dict, db_path: Path
    ):
        _seed_mixed(db_path)
        with get_connection(db_path) as conn:
            _insert_log(conn, _ago(30))
            conn.commit()
        res = await client.post("/api/maintenance/prune", headers=auth_headers)
        assert res.status_code == 200
        body = res.json()
        assert body["deleted_logs"] == 1
        assert body["deleted_history"] == 4
        assert body["deleted_ai_audit"] == 2

    @pytest.mark.asyncio
    async def test_external_prune_reports_separate_counts(
        self, client: AsyncClient, auth_headers: dict, db_path: Path
    ):
        res = await client.post(
            "/api/tokens", json={"name": "ops", "scopes": ["system:write"]}, headers=auth_headers
        )
        token_headers = {"Authorization": f"Bearer {res.json()['raw_token']}"}
        _seed_mixed(db_path)

        res = await client.post("/api/v1/system/prune", headers=token_headers)
        assert res.status_code == 200
        body = res.json()
        assert body["deleted_history"] == 4
        assert body["deleted_ai_audit"] == 2
        assert "deleted_logs" in body


class TestPruneWorkerSchedule:
    @pytest.mark.asyncio
    async def test_worker_publishes_next_cleanup_after_first_run(self, db_path: Path):
        import asyncio

        worker = retention_mod.PruneWorker(db_path)
        task = asyncio.create_task(worker.run())
        try:
            for _ in range(100):
                if get_prune_schedule()["next_cleanup_at"]:
                    break
                await asyncio.sleep(0.05)
            schedule = get_prune_schedule()
            assert schedule["last_cleanup_at"] is not None
            next_at = datetime.datetime.fromisoformat(schedule["next_cleanup_at"])
            delta = (next_at - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
            assert 86000 < delta <= 86400
        finally:
            await worker.stop()
            await asyncio.wait_for(task, timeout=5)
        assert get_prune_schedule()["next_cleanup_at"] is None
