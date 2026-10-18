"""
Tests for prune/vacuum operation guarding, cooldown semantics, generic vacuum errors,
rate limit headers, and the OpenAPI Bearer security scheme.
"""

import asyncio
import logging
import threading
from pathlib import Path
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core import pipeline as pipeline_mod
from app.core.api_tokens import api_token_rate_limiter, _last_used_timestamps
from app.core.migrations import get_connection, run_migrations
from app.core.rate_limiter import login_rate_limiter
from app.core.security import (
    SESSION_COOKIE_NAME,
    create_session_token,
    get_or_create_master_key,
    reset_crypto_cache,
)
from app.core.sse import sse_manager
from app.main import create_app
from app.services.drop_filter import init_drop_filter
from app.services.alert_evaluator import init_alert_evaluator
import app.api.external_v1 as ext_v1_mod
import app.api.ingest as ingest_mod
import app.api.mcp as mcp_api_mod
import app.api.system as system_mod
import app.services.operation_guard as guard_mod
import app.services.retention as retention_mod
from fastapi import HTTPException
from app.services.operation_guard import (
    OperationInProgressError,
    reset_operation_state,
    cooldown_remaining_seconds,
    mark_operation_success,
    operation_guard,
    run_exclusive,
)
from app.services.retention import InsufficientDiskSpaceError


@pytest.fixture(autouse=True)
def reset_test_env(tmp_path: Path, monkeypatch):
    pipeline_mod._log_queue = None
    pipeline_mod._dropped_logs_total = 0
    pipeline_mod._dropped_by_filter_total = 0
    login_rate_limiter.reset()
    api_token_rate_limiter.reset()
    reset_crypto_cache()
    sse_manager.reset()
    _last_used_timestamps.clear()
    reset_operation_state()
    ext_v1_mod._cached_error_stats = None
    ext_v1_mod._cached_error_stats_time = 0.0

    db_file = tmp_path / "logs.db"
    key_file = tmp_path / ".secret_key"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("SECRET_KEY_PATH", str(key_file))
    monkeypatch.delenv("LOGSHED_SECRET_KEY", raising=False)

    run_migrations(db_file)
    get_or_create_master_key(key_file)
    init_drop_filter(db_file)
    init_alert_evaluator(db_file)

    yield

    pipeline_mod._log_queue = None
    login_rate_limiter.reset()
    api_token_rate_limiter.reset()
    _last_used_timestamps.clear()
    reset_operation_state()


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


@pytest_asyncio.fixture
async def full_token_headers(client: AsyncClient, auth_headers: dict):
    res = await client.post(
        "/api/tokens",
        json={"name": "Full Access Automation", "scopes": ["*"]},
        headers=auth_headers,
    )
    raw = res.json()["raw_token"]
    return {"Authorization": f"Bearer {raw}"}


@pytest_asyncio.fixture
async def write_token_headers(client: AsyncClient, auth_headers: dict):
    res = await client.post(
        "/api/tokens",
        json={"name": "Ops", "scopes": ["system:write", "logs:write", "logs:read"]},
        headers=auth_headers,
    )
    return {"Authorization": f"Bearer {res.json()['raw_token']}"}


class TestOperationGuard:
    def test_guard_is_exclusive_and_released_after_error(self):
        with operation_guard("prune"):
            with pytest.raises(OperationInProgressError) as exc_info:
                with operation_guard("vacuum"):
                    pass
            assert exc_info.value.active == "prune"

        with pytest.raises(RuntimeError):
            with operation_guard("vacuum"):
                raise RuntimeError("boom")

        with operation_guard("prune"):
            pass

    def test_cooldown_uses_monotonic_and_starts_only_on_success(self, monkeypatch):
        clock = {"now": 1000.0}
        monkeypatch.setattr(guard_mod.time, "monotonic", lambda: clock["now"])
        monkeypatch.setattr(guard_mod.time, "time", lambda: pytest.fail("wall clock must not be used"))

        assert cooldown_remaining_seconds("prune") == 0
        mark_operation_success("prune")
        assert cooldown_remaining_seconds("prune") == 300
        assert cooldown_remaining_seconds("vacuum") == 0

        clock["now"] += 100.5
        assert cooldown_remaining_seconds("prune") == 200
        clock["now"] += 300
        assert cooldown_remaining_seconds("prune") == 0


class TestOperationContention:
    @pytest.mark.asyncio
    async def test_external_prune_and_vacuum_return_409_when_busy(
        self, client: AsyncClient, write_token_headers: dict
    ):
        with operation_guard("vacuum"):
            res_prune = await client.post("/api/v1/system/prune", headers=write_token_headers)
            res_vac = await client.post("/api/v1/system/vacuum", headers=write_token_headers)
        assert res_prune.status_code == 409
        assert res_vac.status_code == 409
        assert "already in progress" in res_prune.json()["detail"]

        # Busy rejections do not start a cooldown
        res_ok = await client.post("/api/v1/system/prune", headers=write_token_headers)
        assert res_ok.status_code == 200

    @pytest.mark.asyncio
    async def test_internal_prune_and_vacuum_return_409_when_busy(
        self, client: AsyncClient, auth_headers: dict
    ):
        with operation_guard("prune"):
            res_prune = await client.post("/api/maintenance/prune", headers=auth_headers)
            res_vac = await client.post("/api/system/vacuum", headers=auth_headers)
        assert res_prune.status_code == 409
        assert res_vac.status_code == 409

        assert (await client.post("/api/maintenance/prune", headers=auth_headers)).status_code == 200

    @pytest.mark.asyncio
    async def test_guard_released_when_operation_fails(
        self, client: AsyncClient, write_token_headers: dict, monkeypatch
    ):
        async def _boom(*args, **kwargs):
            raise RuntimeError("disk exploded")

        monkeypatch.setattr(ext_v1_mod, "execute_prune_async", _boom)
        res = await client.post("/api/v1/system/prune", headers=write_token_headers)
        assert res.status_code == 500
        with operation_guard("prune"):
            pass

    @pytest.mark.asyncio
    async def test_prune_worker_waits_while_busy_then_runs(self, tmp_path: Path, monkeypatch):
        calls = []

        async def _fake_prune(db_path, retention_days=14, index_pending=True):
            calls.append(retention_days)
            return {}

        monkeypatch.setattr(retention_mod, "execute_prune_async", _fake_prune)
        monkeypatch.setattr(retention_mod, "_BUSY_RETRY_SECONDS", 0.05)

        worker = retention_mod.PruneWorker(tmp_path / "logs.db")
        with operation_guard("vacuum"):
            task = asyncio.create_task(worker.run())
            await asyncio.sleep(0.3)
            assert calls == []

        for _ in range(100):
            if calls:
                break
            await asyncio.sleep(0.02)
        assert len(calls) >= 1

        await worker.stop()
        await asyncio.wait_for(task, timeout=2.0)


class TestCancellationKeepsLock:
    @pytest.mark.asyncio
    async def test_run_exclusive_holds_lock_until_work_finishes_after_cancel(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def _work():
            started.set()
            await release.wait()
            return "done"

        async def _other():
            return "other"

        caller = asyncio.create_task(run_exclusive("prune", _work))
        await started.wait()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller

        # The orphaned work is still running, so the lock must still be held
        with pytest.raises(OperationInProgressError):
            await run_exclusive("vacuum", _other)

        release.set()
        for _ in range(100):
            try:
                assert await run_exclusive("vacuum", _other) == "other"
                break
            except OperationInProgressError:
                await asyncio.sleep(0.01)
        else:
            pytest.fail("lock was never released after the work finished")

    @pytest.mark.asyncio
    async def test_prune_worker_cancellation_keeps_lock_until_prune_returns(self, tmp_path: Path, monkeypatch):
        """B-58: cancelling the PruneWorker mid-prune keeps the shared lock until the prune thread returns."""
        started = threading.Event()
        release = threading.Event()

        def _blocking_prune(db_path, retention_days=14, index_pending=True):
            started.set()
            release.wait(timeout=5)
            return {}

        monkeypatch.setattr(retention_mod, "execute_prune", _blocking_prune)
        worker = retention_mod.PruneWorker(tmp_path / "logs.db")
        task = asyncio.create_task(worker.run())
        try:
            assert await asyncio.to_thread(started.wait, 2)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

            # The prune thread is still running, so a vacuum must not be able to start
            with pytest.raises(OperationInProgressError):
                with operation_guard("vacuum"):
                    pass
        finally:
            release.set()

        for _ in range(100):
            try:
                with operation_guard("vacuum"):
                    break
            except OperationInProgressError:
                await asyncio.sleep(0.02)
        else:
            pytest.fail("lock was never released after the prune finished")

    @pytest.mark.asyncio
    async def test_external_prune_cancellation_does_not_allow_overlap(
        self, write_token_headers: dict, monkeypatch
    ):
        started = asyncio.Event()
        release = asyncio.Event()
        calls = []

        async def _slow_prune(db_path, retention_days=14, index_pending=True):
            calls.append(1)
            started.set()
            await release.wait()
            return {
                "deleted_logs": 0,
                "deleted_metrics": 0,
                "metrics": {
                    "recorded_at": "2026-01-01T00:00:00+00:00",
                    "db_size_bytes": 1,
                    "disk_free_bytes": 1,
                    "disk_total_bytes": 1,
                    "total_logs_count": 0,
                },
            }

        monkeypatch.setattr(ext_v1_mod, "execute_prune_async", _slow_prune)

        request = asyncio.create_task(ext_v1_mod.trigger_prune_v1(token={"scopes": ["system:write"]}))
        await started.wait()
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request

        with pytest.raises(HTTPException) as exc_info:
            await ext_v1_mod.trigger_prune_v1(token={"scopes": ["system:write"]})
        assert exc_info.value.status_code == 409
        assert calls == [1]

        release.set()
        for _ in range(100):
            if guard_mod._in_progress_lock.acquire(blocking=False):
                guard_mod._in_progress_lock.release()
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("lock was never released after the prune finished")

    @pytest.mark.asyncio
    async def test_internal_vacuum_cancellation_does_not_allow_overlap(self, monkeypatch):
        started = asyncio.Event()
        release = asyncio.Event()

        async def _slow_vacuum(db_path, *args, **kwargs):
            started.set()
            await release.wait()
            raise RuntimeError("finished badly")

        monkeypatch.setattr(system_mod, "execute_vacuum", _slow_vacuum)

        request = asyncio.create_task(system_mod.trigger_vacuum(user={}))
        await started.wait()
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request

        with pytest.raises(HTTPException) as exc_info:
            await system_mod.trigger_vacuum(user={})
        assert exc_info.value.status_code == 409

        # A failure in the orphaned work still releases the lock
        release.set()
        for _ in range(100):
            if guard_mod._in_progress_lock.acquire(blocking=False):
                guard_mod._in_progress_lock.release()
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("lock was never released after the vacuum failed")


class TestCooldownAfterSuccess:
    @pytest.mark.asyncio
    async def test_failed_prune_does_not_start_cooldown(
        self, client: AsyncClient, write_token_headers: dict, monkeypatch
    ):
        real_prune = ext_v1_mod.execute_prune_async

        async def _boom(*args, **kwargs):
            raise RuntimeError("fail")

        monkeypatch.setattr(ext_v1_mod, "execute_prune_async", _boom)
        assert (await client.post("/api/v1/system/prune", headers=write_token_headers)).status_code == 500

        monkeypatch.setattr(ext_v1_mod, "execute_prune_async", real_prune)
        assert (await client.post("/api/v1/system/prune", headers=write_token_headers)).status_code == 200

        res = await client.post("/api/v1/system/prune", headers=write_token_headers)
        assert res.status_code == 429
        assert 1 <= int(res.headers["Retry-After"]) <= 300

    @pytest.mark.asyncio
    async def test_failed_vacuum_does_not_start_cooldown(
        self, client: AsyncClient, write_token_headers: dict, monkeypatch
    ):
        real_vacuum = ext_v1_mod.execute_vacuum

        async def _boom(*args, **kwargs):
            raise RuntimeError("fail")

        monkeypatch.setattr(ext_v1_mod, "execute_vacuum", _boom)
        assert (await client.post("/api/v1/system/vacuum", headers=write_token_headers)).status_code == 500

        monkeypatch.setattr(ext_v1_mod, "execute_vacuum", real_vacuum)
        assert (await client.post("/api/v1/system/vacuum", headers=write_token_headers)).status_code == 200
        assert (await client.post("/api/v1/system/vacuum", headers=write_token_headers)).status_code == 429


class TestGenericVacuumErrors:
    @pytest.mark.asyncio
    async def test_external_vacuum_error_is_generic_and_logged(
        self, client: AsyncClient, write_token_headers: dict, monkeypatch, caplog
    ):
        async def _boom(*args, **kwargs):
            raise RuntimeError("unable to open /secret/internal/path.db")

        monkeypatch.setattr(ext_v1_mod, "execute_vacuum", _boom)
        with caplog.at_level(logging.ERROR):
            res = await client.post("/api/v1/system/vacuum", headers=write_token_headers)
        assert res.status_code == 500
        assert "secret" not in res.text
        assert res.json()["detail"] == "Database compaction failed. See server logs for details."
        assert any("/secret/internal/path.db" in (r.exc_text or "") for r in caplog.records)

    @pytest.mark.asyncio
    async def test_internal_vacuum_error_is_generic_and_logged(
        self, client: AsyncClient, auth_headers: dict, monkeypatch, caplog
    ):
        async def _boom(*args, **kwargs):
            raise RuntimeError("unable to open /secret/internal/path.db")

        monkeypatch.setattr(system_mod, "execute_vacuum", _boom)
        with caplog.at_level(logging.ERROR):
            res = await client.post("/api/system/vacuum", headers=auth_headers)
        assert res.status_code == 500
        assert "secret" not in res.text
        assert any("/secret/internal/path.db" in (r.exc_text or "") for r in caplog.records)

    @pytest.mark.asyncio
    async def test_insufficient_disk_space_message_is_still_returned(
        self, client: AsyncClient, write_token_headers: dict, monkeypatch
    ):
        async def _low_disk(*args, **kwargs):
            raise InsufficientDiskSpaceError("Insufficient temporary disk headroom available.")

        monkeypatch.setattr(ext_v1_mod, "execute_vacuum", _low_disk)
        res = await client.post("/api/v1/system/vacuum", headers=write_token_headers)
        assert res.status_code == 400
        assert "headroom" in res.json()["detail"]


class TestRateLimitHeaders:
    @pytest.mark.asyncio
    async def test_success_responses_carry_rate_limit_headers(
        self, client: AsyncClient, write_token_headers: dict
    ):
        res1 = await client.get("/api/v1/logs", headers=write_token_headers)
        assert res1.status_code == 200
        assert res1.headers["X-RateLimit-Limit"] == "120"
        assert res1.headers["X-RateLimit-Remaining"] == "119"
        assert 1 <= int(res1.headers["X-RateLimit-Reset"]) <= 60

        res2 = await client.get("/api/v1/logs", headers=write_token_headers)
        assert res2.headers["X-RateLimit-Remaining"] == "118"

    @pytest.mark.asyncio
    async def test_429_has_retry_after_and_rate_limit_headers(
        self, client: AsyncClient, write_token_headers: dict, monkeypatch
    ):
        monkeypatch.setattr(api_token_rate_limiter, "max_requests", 2)
        for _ in range(2):
            assert (await client.get("/api/v1/logs", headers=write_token_headers)).status_code == 200

        res = await client.get("/api/v1/logs", headers=write_token_headers)
        assert res.status_code == 429
        assert res.headers["X-RateLimit-Limit"] == "2"
        assert res.headers["X-RateLimit-Remaining"] == "0"
        retry_after = int(res.headers["Retry-After"])
        assert 1 <= retry_after <= 60
        assert res.headers["X-RateLimit-Reset"] == str(retry_after)

    @pytest.mark.asyncio
    async def test_ingest_headers_on_success_and_429(
        self, client: AsyncClient, write_token_headers: dict, monkeypatch
    ):
        monkeypatch.setattr(ingest_mod.ingest_rate_limiter, "max_requests", 2)
        body = {"message": "hello", "app_name": "t", "host": "h"}

        res1 = await client.post("/api/v1/ingest", json=body, headers=write_token_headers)
        assert res1.status_code in (200, 202)
        assert res1.headers["X-RateLimit-Limit"] == "2"
        assert res1.headers["X-RateLimit-Remaining"] == "1"

        await client.post("/api/v1/ingest", json=body, headers=write_token_headers)
        res3 = await client.post("/api/v1/ingest", json=body, headers=write_token_headers)
        assert res3.status_code == 429
        assert int(res3.headers["Retry-After"]) >= 1
        assert res3.headers["X-RateLimit-Remaining"] == "0"

    @pytest.mark.asyncio
    async def test_mcp_token_validation_429_carries_headers(
        self, client: AsyncClient, auth_headers: dict, monkeypatch
    ):
        res = await client.post(
            "/api/tokens",
            json={"name": "MCP", "scopes": ["logs:read", "alerts:read", "system:read", "maintenance:write"]},
            headers=auth_headers,
        )
        raw = res.json()["raw_token"]
        monkeypatch.setattr(api_token_rate_limiter, "max_requests", 1)

        await mcp_api_mod.validate_mcp_token(raw)
        with pytest.raises(HTTPException) as exc_info:
            await mcp_api_mod.validate_mcp_token(raw)
        assert exc_info.value.status_code == 429
        assert int(exc_info.value.headers["Retry-After"]) >= 1
        assert exc_info.value.headers["X-RateLimit-Remaining"] == "0"

    @pytest.mark.asyncio
    async def test_auth_errors_unchanged(self, client: AsyncClient, write_token_headers: dict):
        res = await client.get("/api/v1/logs")
        assert res.status_code == 401
        assert res.headers["WWW-Authenticate"] == "Bearer"
        res = await client.get("/api/v1/logs", headers={"Authorization": "Bearer ls_live_bogus"})
        assert res.status_code == 401


class TestOpenApiSecurity:
    @pytest.mark.asyncio
    async def test_openapi_declares_bearer_scheme_on_external_routes(self, client: AsyncClient):
        res = await client.get("/openapi.json")
        assert res.status_code == 200
        spec = res.json()

        schemes = spec["components"]["securitySchemes"]
        bearer = [s for s in schemes.values() if s.get("type") == "http" and s.get("scheme") == "bearer"]
        assert len(bearer) == 1

        for path, method in [
            ("/api/v1/logs", "get"),
            ("/api/v1/system/prune", "post"),
            ("/api/v1/system/vacuum", "post"),
            ("/api/v1/ingest", "post"),
        ]:
            assert spec["paths"][path][method].get("security"), f"{method} {path} is not marked secured"
