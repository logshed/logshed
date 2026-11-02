"""
Main loops of the supervised background workers: one full cycle, an error inside a cycle (the
worker logs it and carries on), and a clean stop. Time is driven by lowering each worker's
intervals and back-offs, never by waiting out the production values.

Covers DailyDigestWorker (and the digest's storage change line), StorageMetricsWorker,
FTSIndexWorker and DockerTailer (reconnecting, and settings changed while running).
"""

import asyncio
import datetime
import logging
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.collectors import docker_collector
from app.collectors.docker_collector import DockerTailer
from app.core.migrations import get_connection, run_migrations
from app.core.pipeline import KeyedMultilineAssembler
from app.services.daily_digest import DailyDigestWorker, compute_daily_digest_rollup, format_bytes
from app.services.fts_indexer import FTSIndexWorker
from app.services.storage_metrics import StorageMetricsWorker


async def _wait_until(condition, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.005)


async def _stopped_within(task: asyncio.Task, seconds: float = 1.0) -> None:
    await asyncio.wait_for(task, timeout=seconds)


@pytest.fixture
def db_file(tmp_path: Path) -> Path:
    p = tmp_path / "logs.db"
    run_migrations(p)
    return p


# ---------------------------------------------------------------------------
# Daily digest
# ---------------------------------------------------------------------------

class TestDigestStorageChangeLine:

    @pytest.mark.parametrize(
        "num_bytes, text",
        [
            (0, "0 B"),
            (1023, "1023 B"),
            (1536, "1.5 KB"),
            (5 * 1024 * 1024, "5.0 MB"),
            (-int(2.5 * 1024 ** 3), "-2.5 GB"),
            (3 * 1024 ** 5, "3072.0 TB"),
        ],
    )
    def test_format_bytes(self, num_bytes, text):
        assert format_bytes(num_bytes) == text

    @pytest.mark.parametrize(
        "past_bytes, current_bytes, line",
        [(10 * 1024 ** 2, 12 * 1024 ** 2, "+2.0 MB"), (12 * 1024 ** 2, 9 * 1024 ** 2, "-3.0 MB"), (4096, 4096, "0 B")],
    )
    def test_storage_change_since_the_window_start(self, db_file, past_bytes, current_bytes, line):
        recorded = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=20)).isoformat()
        conn = get_connection(db_file)
        conn.execute(
            "INSERT INTO storage_metrics (recorded_at, db_size_bytes, disk_free_bytes, disk_total_bytes, total_logs_count) "
            "VALUES (?, ?, 1, 2, 0)",
            (recorded, past_bytes),
        )
        conn.commit()
        with patch("app.services.daily_digest.get_db_footprint_bytes", return_value=current_bytes):
            rollup = compute_daily_digest_rollup(conn, window_hours=24, db_path=db_file)
        conn.close()
        assert rollup["storage_delta_str"] == line

    def test_storage_change_falls_back_to_the_latest_older_sample(self, db_file):
        recorded = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=3)).isoformat()
        conn = get_connection(db_file)
        conn.execute(
            "INSERT INTO storage_metrics (recorded_at, db_size_bytes, disk_free_bytes, disk_total_bytes, total_logs_count) "
            "VALUES (?, 1024, 1, 2, 0)",
            (recorded,),
        )
        conn.commit()
        with patch("app.services.daily_digest.get_db_footprint_bytes", return_value=3072):
            rollup = compute_daily_digest_rollup(conn, window_hours=24, db_path=db_file)
        conn.close()
        assert rollup["storage_delta_str"] == "+2.0 KB"


def _set(db_file: Path, **values: str) -> None:
    conn = get_connection(db_file)
    for key, value in values.items():
        conn.execute(
            "INSERT OR REPLACE INTO system_settings (key, value, updated_at, is_encrypted) VALUES (?, ?, datetime('now'), 0)",
            (key, value),
        )
    conn.commit()
    conn.close()


def _frozen_local_now(hour: int, minute: int):
    fake_now = datetime.datetime.now().astimezone().replace(hour=hour, minute=minute, second=0, microsecond=0)

    class FrozenDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return fake_now

    return patch("datetime.datetime", FrozenDatetime)


class TestDailyDigestWorkerLoop:

    @pytest.fixture(autouse=True)
    def short_intervals(self, monkeypatch):
        monkeypatch.setattr(DailyDigestWorker, "CHECK_INTERVAL", 0.01)
        monkeypatch.setattr(DailyDigestWorker, "ERROR_BACKOFF", 0.01)

    async def test_schedule_is_checked_again_after_each_interval(self, db_file):
        worker = DailyDigestWorker(db_file)
        checks = []
        with patch.object(worker, "_should_run_scheduled", side_effect=lambda: checks.append(1) or False), \
                patch("app.services.daily_digest.run_daily_digest", new_callable=AsyncMock) as run_digest:
            task = asyncio.create_task(worker.run())
            await _wait_until(lambda: len(checks) >= 3)
            await worker.stop()
            await _stopped_within(task)
        run_digest.assert_not_awaited()

    async def test_an_error_in_a_cycle_is_logged_and_the_next_cycle_sends(self, db_file, caplog):
        caplog.set_level(logging.ERROR, logger="app.services.daily_digest")
        worker = DailyDigestWorker(db_file)
        answers = iter([RuntimeError("database is locked"), True])

        def _check():
            answer = next(answers, False)
            if isinstance(answer, Exception):
                raise answer
            return answer

        with patch.object(worker, "_should_run_scheduled", side_effect=_check), \
                patch("app.services.daily_digest.run_daily_digest", new_callable=AsyncMock) as run_digest:
            task = asyncio.create_task(worker.run())
            await _wait_until(lambda: run_digest.await_count >= 1)
            await worker.stop()
            await _stopped_within(task)

        run_digest.assert_awaited_with(db_file, force=False)
        assert any("DailyDigestWorker error" in r.getMessage() and "database is locked" in r.getMessage() for r in caplog.records)

    async def test_stop_ends_the_wait_for_the_next_check(self, db_file, monkeypatch):
        monkeypatch.setattr(DailyDigestWorker, "CHECK_INTERVAL", 30.0)
        worker = DailyDigestWorker(db_file)
        with patch.object(worker, "_should_run_scheduled", return_value=False):
            task = asyncio.create_task(worker.run())
            await asyncio.sleep(0.02)
            await worker.stop()
            await _stopped_within(task, 0.5)

    async def test_stop_during_the_error_backoff_ends_the_worker(self, db_file, monkeypatch):
        monkeypatch.setattr(DailyDigestWorker, "ERROR_BACKOFF", 30.0)
        worker = DailyDigestWorker(db_file)
        with patch.object(worker, "_should_run_scheduled", side_effect=RuntimeError("boom")):
            task = asyncio.create_task(worker.run())
            await asyncio.sleep(0.02)
            await worker.stop()
            await _stopped_within(task, 0.5)

    def test_an_unreadable_schedule_time_means_09_00(self, db_file):
        _set(db_file, daily_digest_enabled="1", daily_digest_schedule_time="half past nine")
        worker = DailyDigestWorker(db_file)
        with _frozen_local_now(8, 59):
            assert worker._should_run_scheduled() is False
        with _frozen_local_now(9, 0):
            assert worker._should_run_scheduled() is True

    def test_an_unreadable_last_run_does_not_block_the_digest(self, db_file):
        _set(db_file, daily_digest_enabled="1", daily_digest_schedule_time="06:00", daily_digest_last_run="not a date")
        worker = DailyDigestWorker(db_file)
        with _frozen_local_now(7, 0):
            assert worker._should_run_scheduled() is True

    def test_a_digest_already_sent_today_is_not_sent_again(self, db_file):
        with _frozen_local_now(10, 0):
            sent_today = datetime.datetime.now().replace(hour=9, minute=0).astimezone(datetime.timezone.utc).isoformat()
        _set(db_file, daily_digest_enabled="1", daily_digest_schedule_time="09:00", daily_digest_last_run=sent_today)
        worker = DailyDigestWorker(db_file)
        with _frozen_local_now(10, 0):
            assert worker._should_run_scheduled() is False


# ---------------------------------------------------------------------------
# Storage metrics
# ---------------------------------------------------------------------------

class TestStorageMetricsWorkerLoop:

    @pytest.fixture(autouse=True)
    def short_intervals(self, monkeypatch):
        monkeypatch.setattr(StorageMetricsWorker, "SAMPLE_INTERVAL", 0.02)
        monkeypatch.setattr(StorageMetricsWorker, "ERROR_BACKOFF", 0.01)

    async def test_samples_again_after_each_interval_and_writes_rows(self, db_file):
        worker = StorageMetricsWorker(db_file)
        task = asyncio.create_task(worker.run())

        def _rows() -> int:
            conn = get_connection(db_file)
            count = conn.execute("SELECT COUNT(*) FROM storage_metrics").fetchone()[0]
            conn.close()
            return count

        await _wait_until(lambda: _rows() >= 2)
        await worker.stop()
        await _stopped_within(task)

    async def test_an_error_in_a_sample_is_logged_and_retried(self, db_file, caplog):
        caplog.set_level(logging.ERROR, logger="app.services.storage_metrics")
        calls = []

        def _record(path):
            calls.append(path)
            if len(calls) == 1:
                raise OSError("disk unavailable")
            return {}

        worker = StorageMetricsWorker(db_file)
        with patch("app.services.storage_metrics.record_metrics", side_effect=_record):
            task = asyncio.create_task(worker.run())
            await _wait_until(lambda: len(calls) >= 2)
            await worker.stop()
            await _stopped_within(task)

        assert any("Error in StorageMetricsWorker loop: disk unavailable" in r.getMessage() for r in caplog.records)

    async def test_stop_ends_the_wait_for_the_next_sample(self, db_file, monkeypatch):
        monkeypatch.setattr(StorageMetricsWorker, "SAMPLE_INTERVAL", 3600.0)
        worker = StorageMetricsWorker(db_file)
        with patch("app.services.storage_metrics.record_metrics", return_value={}) as record:
            task = asyncio.create_task(worker.run())
            await _wait_until(lambda: record.call_count == 1)
            await worker.stop()
            await _stopped_within(task, 0.5)
        assert record.call_count == 1

    async def test_stop_during_the_error_backoff_ends_the_worker(self, db_file, monkeypatch):
        monkeypatch.setattr(StorageMetricsWorker, "ERROR_BACKOFF", 30.0)
        worker = StorageMetricsWorker(db_file)
        with patch("app.services.storage_metrics.record_metrics", side_effect=OSError("disk unavailable")):
            task = asyncio.create_task(worker.run())
            await asyncio.sleep(0.02)
            await worker.stop()
            await _stopped_within(task, 0.5)


# ---------------------------------------------------------------------------
# Full-text indexer
# ---------------------------------------------------------------------------

def _insert_logs(db_file: Path, messages: list[str]) -> None:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = get_connection(db_file)
    conn.executemany(
        "INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, message, raw) "
        "VALUES (?, ?, '10.0.0.1', 'srv', 'app', ?, ?)",
        [(now, now, m, m) for m in messages],
    )
    conn.commit()
    conn.close()


def _matches(db_file: Path, term: str) -> int:
    conn = get_connection(db_file)
    count = conn.execute("SELECT COUNT(*) FROM logs_fts WHERE logs_fts MATCH ?", (term,)).fetchone()[0]
    conn.close()
    return count


class TestFtsIndexWorkerLoop:

    async def test_a_failed_pass_is_logged_and_the_next_pass_indexes(self, db_file, caplog):
        caplog.set_level(logging.ERROR, logger="app.services.fts_indexer")
        _insert_logs(db_file, ["indexloopalpha one", "indexloopalpha two"])
        worker = FTSIndexWorker(db_file, poll_interval=0.01)
        real_chunk = worker._index_pending_chunk
        calls = []

        def _flaky(batch_size):
            calls.append(batch_size)
            if len(calls) == 1:
                raise RuntimeError("database disk image is malformed")
            return real_chunk(batch_size)

        worker._index_pending_chunk = _flaky
        task = asyncio.create_task(worker.run())
        await _wait_until(lambda: _matches(db_file, "indexloopalpha") == 2)
        await worker.stop()
        await _stopped_within(task)

        assert any("FTSIndexWorker error during indexing pass" in r.getMessage() for r in caplog.records)

    async def test_a_paused_worker_indexes_nothing_until_resumed(self, db_file):
        worker = FTSIndexWorker(db_file, poll_interval=0.01)
        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.02)
        await worker.pause_indexing()
        _insert_logs(db_file, ["pausedbravo entry"])
        worker.notify_new_logs()
        await asyncio.sleep(0.05)
        assert _matches(db_file, "pausedbravo") == 0

        worker.resume_indexing()
        await _wait_until(lambda: _matches(db_file, "pausedbravo") == 1)
        await worker.stop()
        await _stopped_within(task)

    async def test_stop_while_paused_ends_the_worker_and_indexes_the_rest(self, db_file):
        worker = FTSIndexWorker(db_file, poll_interval=10.0)
        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.02)
        await worker.pause_indexing()
        _insert_logs(db_file, ["drainedcharlie entry"])

        await worker.stop()
        await _stopped_within(task)
        assert _matches(db_file, "drainedcharlie") == 1


# ---------------------------------------------------------------------------
# Docker tailer
# ---------------------------------------------------------------------------

class _FakeClient:
    is_closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, path, **kwargs):
        return MagicMock(status_code=200)


@pytest.fixture
def tailer_factory(monkeypatch, db_file):
    monkeypatch.setenv("DOCKER_HOST", "tcp://docker.lan:2375")
    monkeypatch.setattr(docker_collector, "_INITIAL_BACKOFF", 0.01)

    def _make(**kwargs) -> DockerTailer:
        return DockerTailer(KeyedMultilineAssembler(), db_path=db_file, alias_cache=MagicMock(), **kwargs)

    return _make


class TestDockerTailerReconnect:

    async def test_a_lost_connection_is_logged_cleaned_up_and_retried(self, tailer_factory, monkeypatch, caplog):
        caplog.set_level(logging.WARNING, logger="app.collectors.docker_collector")
        tailer = tailer_factory()
        attempts = []

        def _build(base_url, uds_path, ssl_context=None):
            attempts.append(base_url)
            if len(attempts) == 1:
                raise OSError("connection refused")
            return _FakeClient()

        connected = asyncio.Event()

        async def _watch(client):
            connected.set()
            await tailer._cancel_event.wait()

        cleanup = AsyncMock()
        monkeypatch.setattr(docker_collector, "_build_client", _build)
        monkeypatch.setattr(tailer, "_attach_running_containers", AsyncMock())
        monkeypatch.setattr(tailer, "_watch_events", _watch)
        monkeypatch.setattr(tailer, "_cleanup_tailers", cleanup)

        task = asyncio.create_task(tailer.run())
        await asyncio.wait_for(connected.wait(), timeout=2)
        assert tailer._is_connected is True and tailer._last_error is None
        await tailer.stop()
        await _stopped_within(task)

        assert len(attempts) == 2
        cleanup.assert_awaited()
        assert any("Docker connection error at http://docker.lan:2375" in r.getMessage() for r in caplog.records)

    async def test_stop_during_the_reconnect_wait_ends_at_once(self, tailer_factory, monkeypatch):
        monkeypatch.setattr(docker_collector, "_INITIAL_BACKOFF", 30.0)
        tailer = tailer_factory()
        failed = asyncio.Event()

        def _build(*args, **kwargs):
            failed.set()
            raise OSError("connection refused")

        monkeypatch.setattr(docker_collector, "_build_client", _build)
        monkeypatch.setattr(tailer, "_cleanup_tailers", AsyncMock())
        task = asyncio.create_task(tailer.run())
        await asyncio.wait_for(failed.wait(), timeout=2)
        await asyncio.sleep(0.01)

        await tailer.stop()
        await _stopped_within(task, 0.5)
        assert tailer._last_error == "connection refused"

    async def test_a_disabled_tailer_waits_and_connects_when_enabled(self, tailer_factory, monkeypatch):
        tailer = tailer_factory(enable_docker=False)
        connected = asyncio.Event()

        async def _watch(client):
            connected.set()
            await tailer._cancel_event.wait()

        monkeypatch.setattr(docker_collector, "_build_client", lambda *a, **k: _FakeClient())
        monkeypatch.setattr(tailer, "_attach_running_containers", AsyncMock())
        monkeypatch.setattr(tailer, "_watch_events", _watch)

        task = asyncio.create_task(tailer.run())
        await asyncio.sleep(0.02)
        assert not connected.is_set()

        await tailer.update_settings(enable_docker=True)
        await asyncio.wait_for(connected.wait(), timeout=2)
        await tailer.stop()
        await _stopped_within(task)


def _fake_tailer_task(name: str) -> asyncio.Task:
    task = asyncio.get_running_loop().create_task(asyncio.sleep(3600), name=f"docker-tail-{name}")
    return task


class TestDockerTailerSettingsChanges:

    async def test_turning_docker_off_stops_every_container_tailer(self, tailer_factory, monkeypatch):
        tailer = tailer_factory(enable_docker=True)
        cleanup = AsyncMock()
        monkeypatch.setattr(tailer, "_cleanup_tailers", cleanup)

        await tailer.update_settings(enable_docker=False)

        assert tailer.enable_docker is False
        cleanup.assert_awaited_once()

    async def test_turning_docker_on_reattaches_through_the_open_client(self, tailer_factory, monkeypatch):
        tailer = tailer_factory(enable_docker=False)
        attach = AsyncMock()
        monkeypatch.setattr(tailer, "_attach_running_containers", attach)
        tailer._current_client = _FakeClient()

        await tailer.update_settings(enable_docker=True)

        assert tailer._settings_event.is_set()
        attach.assert_awaited_once_with(tailer._current_client)

    async def test_a_reattach_failure_on_enable_is_only_logged(self, tailer_factory, monkeypatch, caplog):
        caplog.set_level(logging.WARNING, logger="app.collectors.docker_collector")
        tailer = tailer_factory(enable_docker=False)
        monkeypatch.setattr(tailer, "_attach_running_containers", AsyncMock(side_effect=RuntimeError("daemon gone")))
        tailer._current_client = _FakeClient()

        await tailer.update_settings(enable_docker=True)

        assert tailer.enable_docker is True
        assert any("Error re-attaching containers on enable: daemon gone" in r.getMessage() for r in caplog.records)

    async def test_new_exclusions_stop_matching_tailers_and_attach_the_rest(self, tailer_factory, monkeypatch):
        tailer = tailer_factory(enable_docker=True, exclude_containers="")
        keep, drop = _fake_tailer_task("web"), _fake_tailer_task("watchtower")
        tailer._tailers = {"a" * 64: (keep, asyncio.Event()), "b" * 64: (drop, asyncio.Event())}
        stopped = []

        async def _stop_tailer(cid):
            stopped.append(cid)
            tailer._tailers.pop(cid, None)

        attach = AsyncMock()
        monkeypatch.setattr(tailer, "_stop_tailer", _stop_tailer)
        monkeypatch.setattr(tailer, "_attach_running_containers", attach)
        tailer._current_client = _FakeClient()

        await tailer.update_settings(exclude_containers="watchtower")

        assert stopped == ["b" * 64]
        assert tailer.exclude_containers == "watchtower"
        attach.assert_awaited_once()
        keep.cancel()
        drop.cancel()

    async def test_a_new_source_alias_is_used_and_unchanged_values_change_nothing(self, tailer_factory, monkeypatch, caplog):
        caplog.set_level(logging.INFO, logger="app.collectors.docker_collector")
        tailer = tailer_factory(enable_docker=True, exclude_containers="", source_alias="docker")
        monkeypatch.setattr(tailer, "_cleanup_tailers", AsyncMock())

        await tailer.update_settings(enable_docker=True, exclude_containers="", source_alias="docker")
        assert not any("DockerTailer settings updated" in r.getMessage() for r in caplog.records)

        await tailer.update_settings(source_alias="nas-docker")
        assert tailer.source_alias == "nas-docker"
        assert any("source_alias='nas-docker'" in r.getMessage() for r in caplog.records)
