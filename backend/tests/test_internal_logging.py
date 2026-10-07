"""
Unit and integration tests for LogShed's internal application logging mechanism.
Tests severity level filtering, disabling, recursion/loop suppression, dynamic reconfiguration,
environment variable configuration, and settings API integration.
"""

import asyncio
import concurrent.futures
import logging
from pathlib import Path
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core import pipeline as pipeline_mod
from app.core.config import (
    DEFAULT_INTERNAL_LOG_LEVEL,
    get_internal_log_level,
    get_internal_log_level_name,
    parse_internal_log_level,
    to_canonical_log_level_name,
)
from app.core.migrations import run_migrations
from app.core.pipeline import InternalLogHandler, get_queue
from app.core.security import SESSION_COOKIE_NAME, create_session_token
from app.main import (
    configure_internal_log_handler,
    create_app,
    get_internal_log_handler,
)


@pytest.fixture(autouse=True)
def reset_pipeline_and_handler(monkeypatch):
    """Reset the global queue and internal log handler between tests."""
    pipeline_mod._log_queue = None
    pipeline_mod._dropped_logs_total = 0
    monkeypatch.delenv("LOGSHED_INTERNAL_LOG_LEVEL", raising=False)
    yield
    pipeline_mod._log_queue = None
    pipeline_mod._dropped_logs_total = 0
    configure_internal_log_handler("DISABLED")


class TestInternalLogLevelConfig:
    """Tests for config parsing and environment variable resolution."""

    def test_default_internal_log_level_is_warning(self):
        assert DEFAULT_INTERNAL_LOG_LEVEL == logging.WARNING
        assert get_internal_log_level() == logging.WARNING
        assert get_internal_log_level_name() == "WARNING"

    @pytest.mark.parametrize(
        "val,expected",
        [
            ("DEBUG", logging.DEBUG),
            ("debug", logging.DEBUG),
            ("  DeBuG  ", logging.DEBUG),
            ("INFO", logging.INFO),
            ("info", logging.INFO),
            ("WARNING", logging.WARNING),
            ("WARN", logging.WARNING),
            ("warn", logging.WARNING),
            ("ERROR", logging.ERROR),
            ("error", logging.ERROR),
            ("CRITICAL", logging.CRITICAL),
            ("FATAL", logging.CRITICAL),
            ("fatal", logging.CRITICAL),
            ("DISABLED", None),
            ("disabled", None),
            ("OFF", None),
            ("off", None),
            ("NONE", None),
            ("none", None),
            ("FALSE", None),
            ("0", None),
            (logging.DEBUG, logging.DEBUG),
            (logging.ERROR, logging.ERROR),
        ],
    )
    def test_parse_internal_log_level_valid_values(self, val, expected):
        assert parse_internal_log_level(val) == expected

    @pytest.mark.parametrize(
        "val",
        [None, "", "   "],
    )
    def test_parse_internal_log_level_none_or_empty_returns_default(self, val):
        assert parse_internal_log_level(val) == logging.WARNING

    @pytest.mark.parametrize(
        "val",
        ["INVALID", "NOT_A_LEVEL", "super_debug", "12345xyz"],
    )
    def test_parse_internal_log_level_unrecognized_returns_default(self, val):
        assert parse_internal_log_level(val) == logging.WARNING

    def test_get_internal_log_level_env_override(self, monkeypatch):
        monkeypatch.setenv("LOGSHED_INTERNAL_LOG_LEVEL", "ERROR")
        assert get_internal_log_level() == logging.ERROR
        assert get_internal_log_level_name() == "ERROR"

        monkeypatch.setenv("LOGSHED_INTERNAL_LOG_LEVEL", "DISABLED")
        assert get_internal_log_level() is None
        assert get_internal_log_level_name() == "DISABLED"

        monkeypatch.setenv("LOGSHED_INTERNAL_LOG_LEVEL", "INFO")
        assert get_internal_log_level() == logging.INFO
        assert get_internal_log_level_name() == "INFO"

    @pytest.mark.parametrize(
        "val,expected",
        [
            ("warn", "WARNING"),
            ("WARNING", "WARNING"),
            ("fatal", "CRITICAL"),
            ("critical", "CRITICAL"),
            ("off", "DISABLED"),
            ("none", "DISABLED"),
            ("0", "DISABLED"),
            ("false", "DISABLED"),
            ("disabled", "DISABLED"),
            ("debug", "DEBUG"),
            ("info", "INFO"),
            ("error", "ERROR"),
            (None, "WARNING"),
            (logging.INFO, "INFO"),
        ],
    )
    def test_to_canonical_log_level_name(self, val, expected):
        assert to_canonical_log_level_name(val) == expected


class TestInternalLogHandler:
    """Tests for InternalLogHandler filtering, formatting, re-entrancy, and loop suppression."""

    def _make_record(
        self,
        name: str = "app.collectors.syslog",
        level: int = logging.INFO,
        msg: str = "Test message",
        exc_info=None,
    ) -> logging.LogRecord:
        return logging.LogRecord(
            name=name,
            level=level,
            pathname="test_file.py",
            lineno=42,
            msg=msg,
            args=(),
            exc_info=exc_info,
        )

    def test_default_level_ignores_debug_and_info(self):
        handler = InternalLogHandler()
        assert handler.level == logging.WARNING
        assert handler.is_disabled is False
        q = get_queue()

        # Emit DEBUG -> ignored
        handler.emit(self._make_record(level=logging.DEBUG, msg="Debug log"))
        assert q.empty()

        # Emit INFO -> ignored
        handler.emit(self._make_record(level=logging.INFO, msg="Info log"))
        assert q.empty()

        # Emit WARNING -> captured
        handler.emit(self._make_record(level=logging.WARNING, msg="Warning log"))
        assert not q.empty()
        entry = q.get_nowait()
        assert entry["severity"] == 4
        assert entry["message"] == "Warning log"
        assert entry["app_name"] == "syslog"
        assert entry["source_alias"] == "logshed"

        # Emit ERROR -> captured
        handler.emit(self._make_record(level=logging.ERROR, msg="Error log"))
        entry = q.get_nowait()
        assert entry["severity"] == 3

        # Emit CRITICAL -> captured
        handler.emit(self._make_record(level=logging.CRITICAL, msg="Critical log"))
        entry = q.get_nowait()
        assert entry["severity"] == 2

    def test_explicit_debug_level_captures_all(self):
        handler = InternalLogHandler(level="DEBUG")
        assert handler.level == logging.DEBUG
        q = get_queue()

        handler.emit(self._make_record(level=logging.DEBUG, msg="Debug msg"))
        assert not q.empty()
        entry = q.get_nowait()
        assert entry["severity"] == 7
        assert entry["message"] == "Debug msg"

        handler.emit(self._make_record(level=logging.INFO, msg="Info msg"))
        entry = q.get_nowait()
        assert entry["severity"] == 6

    def test_explicit_error_level_filters_warning(self):
        handler = InternalLogHandler(level="ERROR")
        assert handler.level == logging.ERROR
        q = get_queue()

        handler.emit(self._make_record(level=logging.WARNING, msg="Warn"))
        assert q.empty()

        handler.emit(self._make_record(level=logging.ERROR, msg="Err"))
        assert not q.empty()
        entry = q.get_nowait()
        assert entry["severity"] == 3

    def test_disabled_handler_captures_nothing(self):
        handler = InternalLogHandler(level="DISABLED")
        assert handler.is_disabled is True
        q = get_queue()

        handler.emit(self._make_record(level=logging.CRITICAL, msg="Crit"))
        handler.emit(self._make_record(level=logging.ERROR, msg="Err"))
        assert q.empty()

    def test_dynamic_reconfiguration(self):
        handler = InternalLogHandler(level="ERROR")
        q = get_queue()

        handler.emit(self._make_record(level=logging.WARNING, msg="Warn 1"))
        assert q.empty()

        # Lower to WARNING
        handler.set_internal_level("WARNING")
        assert handler.is_disabled is False
        assert handler.level == logging.WARNING

        handler.emit(self._make_record(level=logging.WARNING, msg="Warn 2"))
        assert not q.empty()
        entry = q.get_nowait()
        assert entry["message"] == "Warn 2"

        # Disable
        handler.set_internal_level("OFF")
        assert handler.is_disabled is True

        handler.emit(self._make_record(level=logging.CRITICAL, msg="Crit 2"))
        assert q.empty()

    def test_exc_info_formatting(self):
        handler = InternalLogHandler(level="ERROR")
        q = get_queue()

        try:
            raise ValueError("Intentional explosion for testing")
        except ValueError:
            import sys
            exc_info = sys.exc_info()

        handler.emit(self._make_record(level=logging.ERROR, msg="Failure occurred", exc_info=exc_info))
        assert not q.empty()
        entry = q.get_nowait()
        assert "Failure occurred" in entry["message"]
        assert "Intentional explosion for testing" in entry["message"]
        assert "Traceback (most recent call last):" in entry["message"]
        assert "Traceback" in entry["raw"]

    def test_internal_log_handler_resolves_alias_from_alias_cache_for_127_0_0_1(self, tmp_path):
        from app.collectors.syslog import AliasCache
        db_file = tmp_path / "test_alias.db"
        run_migrations(db_file)
        import sqlite3
        with sqlite3.connect(str(db_file)) as conn:
            conn.execute("INSERT INTO host_aliases (ip, alias, created_at) VALUES ('127.0.0.1', 'Local', '2026-09-01T00:00:00Z')")
            conn.commit()

        cache = AliasCache(db_file)
        handler = InternalLogHandler(level="DEBUG", alias_cache=cache)
        q = get_queue()

        handler.emit(self._make_record(level=logging.INFO, msg="Internal startup log"))
        assert not q.empty()
        entry = q.get_nowait()
        assert entry["source_ip"] == "127.0.0.1"
        assert entry["source_alias"] == "Local"

    def test_internal_log_handler_resolves_alias_from_alias_cache_for_logshed_name(self, tmp_path):
        from app.collectors.syslog import AliasCache
        db_file = tmp_path / "test_alias_name.db"
        run_migrations(db_file)
        import sqlite3
        with sqlite3.connect(str(db_file)) as conn:
            conn.execute("INSERT INTO host_aliases (ip, alias, created_at) VALUES ('logshed', 'Local-Shed', '2026-09-01T00:00:00Z')")
            conn.commit()

        cache = AliasCache(db_file)
        handler = InternalLogHandler(level="DEBUG", alias_cache=cache)
        q = get_queue()

        handler.emit(self._make_record(level=logging.INFO, msg="Named internal log"))
        assert not q.empty()
        entry = q.get_nowait()
        assert entry["source_alias"] == "Local-Shed"

    def test_internal_log_handler_falls_back_to_logshed_when_unaliased(self):
        handler = InternalLogHandler(level="DEBUG")
        q = get_queue()

        handler.emit(self._make_record(level=logging.INFO, msg="Default unaliased internal log"))
        assert not q.empty()
        entry = q.get_nowait()
        assert entry["source_ip"] == "127.0.0.1"
        assert entry["source_alias"] == "logshed"

    def test_loop_suppression_ignored_loggers(self):
        handler = InternalLogHandler(level="DEBUG")
        q = get_queue()

        ignored_names = [
            "uvicorn.access",
            "uvicorn.access.client",
            "httpcore",
            "httpcore.connection",
            "httpx",
            "httpx.client",
            "asyncio",
            "app.core.pipeline",
            "app.core.pipeline.QueueConsumer",
            "app.core.sse",
            "app.core.migrations",
            "app.services.retention",
            "app.services.storage_metrics",
        ]

        for name in ignored_names:
            handler.emit(self._make_record(name=name, level=logging.CRITICAL, msg=f"From {name}"))

        assert q.empty(), f"Expected queue to be empty, but contained items: {[q.get_nowait() for _ in range(q.qsize())]}"

    def test_reentrancy_protection(self):
        """Verify that recursive calls to emit on the same thread do not loop."""
        handler = InternalLogHandler(level="DEBUG")
        q = get_queue()

        class RecursiveRecord(logging.LogRecord):
            def getMessage(self):
                # Trigger a nested emit call while inside emit
                nested_record = logging.LogRecord(
                    name="app.main",
                    level=logging.ERROR,
                    pathname="test.py",
                    lineno=1,
                    msg="Nested error",
                    args=(),
                    exc_info=None,
                )
                handler.emit(nested_record)
                return "Outer message"

        rec = RecursiveRecord(
            name="app.main",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="Outer message",
            args=(),
            exc_info=None,
        )

        handler.emit(rec)
        # Should have captured the outer message and prevented the recursive nested emit
        assert not q.empty()
        entries = []
        while not q.empty():
            entries.append(q.get_nowait())
        assert len(entries) == 1
        assert entries[0]["message"] == "Outer message"

    def test_thread_safe_emission(self):
        """Verify emission from worker threads does not raise and properly enqueues."""
        handler = InternalLogHandler(level="WARNING")
        q = get_queue()

        def _worker(idx: int):
            rec = self._make_record(
                name="app.collectors.syslog",
                level=logging.WARNING,
                msg=f"Thread message {idx}",
            )
            handler.emit(rec)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(_worker, i) for i in range(20)]
            concurrent.futures.wait(futures)

        assert q.qsize() == 20

    def test_internal_log_preserves_raw_message(self):
        """InternalLogHandler retains raw messages without redundant redaction (redaction occurs on-demand for AI prompts)."""
        q = get_queue()
        while not q.empty():
            q.get_nowait()

        handler = InternalLogHandler(level="DEBUG")
        rec = logging.LogRecord(
            name="app.collectors.docker",
            level=logging.ERROR,
            pathname="docker.py",
            lineno=42,
            msg="Failed auth with password=SuperSecretPassword123 and api_key=AIzaSyD-1234567890abcdef",
            args=(),
            exc_info=None,
        )
        handler.emit(rec)
        assert q.qsize() == 1
        entry = q.get_nowait()
        assert "SuperSecretPassword123" in entry["message"]
        assert "AIzaSyD-1234567890abcdef" in entry["message"]


    @pytest.mark.asyncio
    async def test_internal_log_end_to_end_db_persistence(self, tmp_path: Path):
        """End-to-end verification that logger.warning persists to SQLite via QueueConsumer."""
        from app.core.pipeline import QueueConsumer
        from app.core.migrations import get_connection

        db_file = tmp_path / "internal_logs.db"
        run_migrations(db_file)
        consumer = QueueConsumer(db_file)

        # Configure handler at WARNING
        handler = configure_internal_log_handler("WARNING")
        assert handler is not None

        try:
            # Emit an info log (should be ignored) and a warning log (should be captured)
            test_logger = logging.getLogger("app.collectors.syslog")
            test_logger.info("This info log should be filtered out by default WARNING level")
            test_logger.warning("Syslog packet malformed on port 1514")

            # Flush queue to SQLite via consumer stop
            await consumer.stop()

            # Query database
            with get_connection(db_file) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT source_alias, app_name, severity, message FROM logs")
                rows = cursor.fetchall()

            assert len(rows) == 1
            assert rows[0][0] == "logshed"
            assert rows[0][1] == "syslog"
            assert rows[0][2] == 4  # RFC 5424 Warning
            assert "Syslog packet malformed on port 1514" in rows[0][3]

        finally:
            configure_internal_log_handler("DISABLED")

    @pytest.mark.asyncio
    async def test_internal_log_end_to_end_info_level(self, tmp_path: Path):
        """End-to-end verification that INFO level captures both info and warning logs."""
        from app.core.pipeline import QueueConsumer
        from app.core.migrations import get_connection

        db_file = tmp_path / "internal_logs_info.db"
        run_migrations(db_file)
        consumer = QueueConsumer(db_file)

        handler = configure_internal_log_handler("INFO")
        assert handler is not None

        try:
            test_logger = logging.getLogger("app.collectors.docker_collector")
            test_logger.info("Docker container nginx discovered")
            test_logger.error("Docker daemon socket connection lost")

            await consumer.stop()

            with get_connection(db_file) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT source_alias, app_name, severity, message FROM logs ORDER BY id ASC")
                rows = cursor.fetchall()

            assert len(rows) == 2
            assert rows[0][1] == "docker_collector"
            assert rows[0][2] == 6  # Informational
            assert "Docker container nginx discovered" in rows[0][3]

            assert rows[1][1] == "docker_collector"
            assert rows[1][2] == 3  # Error
            assert "Docker daemon socket connection lost" in rows[1][3]

        finally:
            configure_internal_log_handler("DISABLED")



class TestSettingsApiInternalLogLevel:
    """Tests for settings API integration of internal_log_level."""

    @pytest_asyncio.fixture
    async def client(self, tmp_path: Path, monkeypatch):
        db_file = tmp_path / "logs.db"
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("DB_PATH", str(db_file))
        run_migrations(db_file)

        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-Requested-With": "XMLHttpRequest"},
        ) as ac:
            token = create_session_token("admin")
            ac.cookies.set(SESSION_COOKIE_NAME, token)
            yield ac

    @pytest.mark.asyncio
    async def test_get_settings_returns_internal_log_level_default(self, client: AsyncClient):
        res = await client.get("/api/settings")
        assert res.status_code == 200
        data = res.json()
        assert "internal_log_level" in data
        assert data["internal_log_level"] == "WARNING"

    @pytest.mark.asyncio
    async def test_get_settings_respects_env_default(self, client: AsyncClient, monkeypatch):
        monkeypatch.setenv("LOGSHED_INTERNAL_LOG_LEVEL", "ERROR")
        res = await client.get("/api/settings")
        assert res.status_code == 200
        data = res.json()
        assert data["internal_log_level"] == "ERROR"

    @pytest.mark.asyncio
    async def test_update_settings_sets_and_persists_internal_log_level(self, client: AsyncClient):
        # Update to INFO
        res_post = await client.post("/api/settings", json={"internal_log_level": "INFO"})
        assert res_post.status_code == 200

        # GET should reflect new level
        res_get = await client.get("/api/settings")
        assert res_get.status_code == 200
        assert res_get.json()["internal_log_level"] == "INFO"

        # Update to DISABLED
        res_post2 = await client.post("/api/settings", json={"internal_log_level": "DISABLED"})
        assert res_post2.status_code == 200

        res_get2 = await client.get("/api/settings")
        assert res_get2.status_code == 200
        assert res_get2.json()["internal_log_level"] == "DISABLED"

    @pytest.mark.asyncio
    async def test_update_settings_rejects_invalid_log_level(self, client: AsyncClient):
        res = await client.post("/api/settings", json={"internal_log_level": "NOT_A_VALID_LEVEL"})
        assert res.status_code == 422
        data = res.json()
        assert "internal_log_level" in str(data)

    @pytest.mark.asyncio
    async def test_update_settings_normalizes_aliases_to_canonical_name(self, client: AsyncClient):
        # Update with alias 'warn' -> should normalize to 'WARNING'
        res_post = await client.post("/api/settings", json={"internal_log_level": "warn"})
        assert res_post.status_code == 200

        res_get = await client.get("/api/settings")
        assert res_get.status_code == 200
        assert res_get.json()["internal_log_level"] == "WARNING"

        # Update with alias 'off' -> should normalize to 'DISABLED'
        res_post2 = await client.post("/api/settings", json={"internal_log_level": "off"})
        assert res_post2.status_code == 200

        res_get2 = await client.get("/api/settings")
        assert res_get2.status_code == 200
        assert res_get2.json()["internal_log_level"] == "DISABLED"


class TestAppLoggerLevelPreservation:
    """Verify that internal log handler configuration does not suppress console info logs."""

    def test_configure_internal_log_handler_preserves_app_logger_level_for_console(self):
        app_logger = logging.getLogger("app")

        # 1. Setting WARNING should keep app logger at NOTSET so root console handler gets INFO logs
        configure_internal_log_handler("WARNING")
        assert app_logger.level == logging.NOTSET
        handler = get_internal_log_handler()
        assert handler is not None
        assert handler.level == logging.WARNING

        # 2. Setting ERROR should keep app logger at NOTSET
        configure_internal_log_handler("ERROR")
        assert app_logger.level == logging.NOTSET
        assert handler.level == logging.ERROR

        # 3. Setting DEBUG should lower app logger to DEBUG so debug logs reach handlers
        configure_internal_log_handler("DEBUG")
        assert app_logger.level == logging.DEBUG
        assert handler.level == logging.DEBUG

        # 4. Switching back to WARNING should restore app logger to NOTSET
        configure_internal_log_handler("WARNING")
        assert app_logger.level == logging.NOTSET

        # 5. Switching to DISABLED should restore app logger to NOTSET
        configure_internal_log_handler("DISABLED")
        assert app_logger.level == logging.NOTSET
        assert get_internal_log_handler() is None
