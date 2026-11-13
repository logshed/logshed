"""
Start-up paths: Docker hosts seeded from DOCKER_ADDITIONAL_HOSTS when the app starts, and one
cycle of the background model refresh worker.

The lifespan runs for real with Docker collection stubbed out (seeded hosts would otherwise be
contacted), and the model refresh worker runs with its sleeps and provider calls replaced.
"""

import asyncio
import json
import logging
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

import app.main as main_mod
from app.core import pipeline as pipeline_mod
from app.core.config import get_docker_additional_hosts
from app.core.migrations import DatabaseNewerThanAppError, get_connection, run_migrations
from app.core.security import decrypt_value, encrypt_value, get_or_create_master_key, reset_crypto_cache

CLIENT_KEY = "-----BEGIN PRIVATE KEY-----\nMIIBVQIBADANBgkqhkiG9w0BAQEFAASCAT8wggE7AgEAAkEA\n-----END PRIVATE KEY-----"


@pytest.fixture
def startup_env(tmp_path: Path, monkeypatch, free_syslog_port) -> Path:
    pipeline_mod._log_queue = None
    reset_crypto_cache()
    db_file = tmp_path / "logs.db"
    key_file = tmp_path / ".secret_key"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("SECRET_KEY_PATH", str(key_file))
    monkeypatch.delenv("LOGSHED_SECRET_KEY", raising=False)
    monkeypatch.setenv("ENABLE_DOCKER", "false")
    run_migrations(db_file)
    get_or_create_master_key(key_file)
    # The seeded hosts are real network URLs; the collector pool must not contact them
    monkeypatch.setattr(main_mod.DockerCollectorPool, "start", AsyncMock())
    yield db_file
    pipeline_mod._log_queue = None
    reset_crypto_cache()


def _additional_hosts() -> str:
    return json.dumps([
        {
            "name": "nas",
            "url": "tcp://192.168.1.20:2376",
            "source_alias": "nas-docker",
            "tls_enabled": "yes",
            "ca_cert": "CA PEM",
            "client_cert": "CERT PEM",
            "client_key": CLIENT_KEY,
            "exclude_containers": "watchtower, *-backup",
        },
        {"name": "pi", "url": "tcp://socket-proxy.lan:2375", "enabled": "off"},
        "not an object",
        {"name": "no-url"},
        {"url": "tcp://10.0.0.9:2375"},
    ])


def _endpoints(db_file: Path) -> dict[str, sqlite3.Row]:
    conn = get_connection(db_file)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM docker_endpoints").fetchall()
    conn.close()
    return {row["name"]: row for row in rows}


async def _start_and_stop() -> None:
    app = main_mod.create_app.uncached()
    async with main_mod.lifespan(app):
        pass


class TestDockerHostsFromTheEnvironment:

    async def test_hosts_are_created_with_the_client_key_encrypted(self, startup_env, monkeypatch):
        monkeypatch.setenv("DOCKER_ADDITIONAL_HOSTS", _additional_hosts())

        await _start_and_stop()

        rows = _endpoints(startup_env)
        assert {"nas", "pi"} <= set(rows)
        nas, pi = rows["nas"], rows["pi"]
        assert (nas["url"], nas["source_alias"], nas["enabled"], nas["tls_enabled"]) == (
            "tcp://192.168.1.20:2376", "nas-docker", 1, 1
        )
        assert (nas["ca_cert"], nas["client_cert"], nas["exclude_containers"]) == ("CA PEM", "CERT PEM", "watchtower, *-backup")
        assert nas["client_key"] != CLIENT_KEY and "PRIVATE KEY" not in nas["client_key"]
        assert decrypt_value(nas["client_key"]) == CLIENT_KEY
        assert (pi["source_alias"], pi["enabled"], pi["tls_enabled"], pi["client_key"]) == ("docker", 0, 0, None)
        assert "no-url" not in rows

    async def test_a_second_start_does_not_duplicate_or_overwrite_them(self, startup_env, monkeypatch):
        monkeypatch.setenv("DOCKER_ADDITIONAL_HOSTS", _additional_hosts())
        await _start_and_stop()
        conn = get_connection(startup_env)
        conn.execute("UPDATE docker_endpoints SET source_alias = 'renamed-in-ui' WHERE name = 'nas'")
        conn.commit()
        conn.close()

        await _start_and_stop()

        conn = get_connection(startup_env)
        counts = dict(conn.execute("SELECT name, COUNT(*) FROM docker_endpoints GROUP BY name").fetchall())
        conn.close()
        assert counts["nas"] == 1 and counts["pi"] == 1
        assert _endpoints(startup_env)["nas"]["source_alias"] == "renamed-in-ui"

    def test_bad_entries_are_skipped_with_a_warning(self, monkeypatch, caplog):
        monkeypatch.setenv("DOCKER_ADDITIONAL_HOSTS", _additional_hosts())
        caplog.set_level(logging.WARNING, logger="app.core.config")

        hosts = get_docker_additional_hosts()

        assert [h["name"] for h in hosts] == ["nas", "pi"]
        warnings = [r.getMessage() for r in caplog.records]
        assert "DOCKER_ADDITIONAL_HOSTS entry 3 is not a JSON object and is skipped." in warnings
        assert "DOCKER_ADDITIONAL_HOSTS entry 4 has no name or url and is skipped." in warnings
        assert "DOCKER_ADDITIONAL_HOSTS entry 5 has no name or url and is skipped." in warnings

    @pytest.mark.parametrize("raw, message", [
        ("{not json", "Invalid JSON in DOCKER_ADDITIONAL_HOSTS"),
        ('{"name": "nas", "url": "tcp://nas:2375"}', "must be a JSON array of objects"),
    ])
    def test_a_value_that_is_not_a_json_array_is_ignored_with_a_warning(self, monkeypatch, caplog, raw, message):
        monkeypatch.setenv("DOCKER_ADDITIONAL_HOSTS", raw)
        caplog.set_level(logging.WARNING, logger="app.core.config")
        assert get_docker_additional_hosts() == []
        assert any(message in r.getMessage() for r in caplog.records)


def _store_settings(db_file: Path, values: dict[str, tuple[str, bool]]) -> None:
    conn = get_connection(db_file)
    for key, (value, encrypted) in values.items():
        conn.execute(
            "INSERT OR REPLACE INTO system_settings (key, value, updated_at, is_encrypted) VALUES (?, ?, datetime('now'), ?)",
            (key, encrypt_value(value) if encrypted else value, 1 if encrypted else 0),
        )
    conn.commit()
    conn.close()


def _one_cycle_sleeps() -> tuple[list[float], object]:
    """A stand-in for the worker's sleeps: the start-up wait returns at once and the 12-hour wait ends the loop."""
    waits: list[float] = []

    async def _sleep(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) > 1:
            raise asyncio.CancelledError

    return waits, _sleep


class TestModelRefreshWorker:

    MODELS = [{"id": "gpt-6.1-sol", "name": "gpt-6.1-sol", "description": None, "supports_thinking": True, "is_deprecated": False}]

    async def _run_one_cycle(self, db_file: Path, fetch: AsyncMock) -> list[float]:
        waits, fake_sleep = _one_cycle_sleeps()
        with patch("app.main.asyncio.sleep", side_effect=fake_sleep), \
                patch("app.services.ai_engine.fetch_available_models", fetch):
            await asyncio.wait_for(main_mod._model_refresh_worker(db_file), timeout=5)
        return waits

    def _cache(self, db_file: Path, provider: str):
        conn = get_connection(db_file)
        row = conn.execute("SELECT value FROM system_settings WHERE key = ?", (f"ai_models_cache_{provider}",)).fetchone()
        conn.close()
        return json.loads(row[0]) if row else None

    async def test_a_cycle_refreshes_the_active_provider_and_caches_its_models(self, startup_env):
        _store_settings(startup_env, {
            "ai_provider": ("openai", False),
            "ai_api_key_openai": ("sk-test-key", True),
            "ai_base_url_openai": ("https://api.openai.com/v1", False),
        })
        fetch = AsyncMock(return_value=self.MODELS)

        waits = await self._run_one_cycle(startup_env, fetch)

        assert waits == [60, 12 * 3600]
        fetch.assert_awaited_once_with("openai", api_key="sk-test-key", base_url="https://api.openai.com/v1")
        assert self._cache(startup_env, "openai") == self.MODELS

    async def test_a_provider_without_a_key_is_not_queried(self, startup_env):
        _store_settings(startup_env, {"ai_provider": ("gemini", False)})
        fetch = AsyncMock(return_value=self.MODELS)

        waits = await self._run_one_cycle(startup_env, fetch)

        fetch.assert_not_awaited()
        assert waits == [60, 12 * 3600]
        assert self._cache(startup_env, "gemini") is None

    async def test_an_openai_compatible_server_is_queried_without_a_key(self, startup_env):
        _store_settings(startup_env, {
            "ai_provider": ("openai_compatible", False),
            "ai_base_url_openai_compatible": ("http://host.docker.internal:11434/v1", False),
        })
        fetch = AsyncMock(return_value=[{**self.MODELS[0], "id": "llama3.2", "name": "llama3.2"}])

        await self._run_one_cycle(startup_env, fetch)

        fetch.assert_awaited_once_with("openai_compatible", api_key=None, base_url="http://host.docker.internal:11434/v1")
        assert [m["id"] for m in self._cache(startup_env, "openai_compatible")] == ["llama3.2"]

    async def test_a_failed_refresh_keeps_the_old_cache_and_the_worker_running(self, startup_env):
        _store_settings(startup_env, {
            "ai_provider": ("anthropic", False),
            "ai_api_key_anthropic": ("sk-ant-test", True),
            "ai_models_cache_anthropic": (json.dumps(self.MODELS), False),
        })
        fetch = AsyncMock(side_effect=RuntimeError("provider unavailable"))

        waits = await self._run_one_cycle(startup_env, fetch)

        assert waits == [60, 12 * 3600]
        assert self._cache(startup_env, "anthropic") == self.MODELS


class TestDatabaseNewerThanApp:
    async def test_start_up_fails_with_the_message_and_starts_nothing(self, startup_env, monkeypatch, caplog):
        conn = sqlite3.connect(str(startup_env))
        conn.execute("PRAGMA user_version = 99")
        conn.close()
        before = startup_env.read_bytes()
        master_key = Mock()
        queue_consumer = Mock()
        syslog_server = Mock()
        monkeypatch.setattr(main_mod, "get_or_create_master_key", master_key)
        monkeypatch.setattr(main_mod, "QueueConsumer", queue_consumer)
        monkeypatch.setattr(main_mod, "SyslogServer", syslog_server)

        with caplog.at_level(logging.ERROR, logger="app.main"):
            with pytest.raises(DatabaseNewerThanAppError):
                await _start_and_stop()

        errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR and r.name == "app.main"]
        assert len(errors) == 1
        assert str(startup_env) in errors[0] and "schema 99" in errors[0]
        assert "Going back to an older version" in errors[0]
        master_key.assert_not_called()
        queue_consumer.assert_not_called()
        syslog_server.assert_not_called()
        assert startup_env.read_bytes() == before


class TestBackupBeforeMigrationFails:
    async def test_start_up_fails_with_the_space_figures_and_migrates_nothing(self, startup_env, monkeypatch, caplog):
        import collections

        import app.core.migrations as migrations_mod
        from app.core import backup as db_backup
        from app.core.migrations import MIGRATIONS, MigrationBackupError

        monkeypatch.delenv("AUTO_BACKUP_ON_MIGRATION", raising=False)
        monkeypatch.setattr(migrations_mod, "MIGRATIONS", list(MIGRATIONS) + [(4, lambda conn: None)])
        usage = collections.namedtuple("usage", "total used free")
        monkeypatch.setattr(db_backup.shutil, "disk_usage", lambda p: usage(50 * 1024**3, 50 * 1024**3, 0))
        queue_consumer = Mock()
        syslog_server = Mock()
        monkeypatch.setattr(main_mod, "QueueConsumer", queue_consumer)
        monkeypatch.setattr(main_mod, "SyslogServer", syslog_server)

        with caplog.at_level(logging.ERROR, logger="app.main"):
            with pytest.raises(MigrationBackupError):
                await _start_and_stop()

        errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR and r.name == "app.main"]
        assert len(errors) == 1
        assert "from schema 3 to 4" in errors[0] and "AUTO_BACKUP_ON_MIGRATION=false" in errors[0]
        queue_consumer.assert_not_called()
        syslog_server.assert_not_called()
        conn = sqlite3.connect(str(startup_env))
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        conn.close()
        assert not (startup_env.parent / "backups").exists()
