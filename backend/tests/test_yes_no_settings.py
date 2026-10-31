"""Yes/no settings and environment variables share one parser and one set of accepted words."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.collectors.docker_collector import DockerTailer, _parse_docker_host
from app.collectors.docker_pool import DockerCollectorPool
from app.core.config import _cast_bool, env_bool, get_docker_additional_hosts, parse_bool, resolve_setting
from app.core.migrations import run_migrations
from app.core.pipeline import KeyedMultilineAssembler
from app.services.ai_service import is_ai_enabled, resolve_ai_provider_settings
from app.services.daily_digest import is_daily_digest_enabled


class TestParseBool:
    @pytest.mark.parametrize(
        "value, expected",
        [
            ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("Yes", True),
            ("on", True), ("ON", True), ("enabled", True), (" true ", True), (True, True), (1, True),
            ("0", False), ("false", False), ("False", False), ("no", False), ("NO", False),
            ("off", False), ("Off", False), ("disabled", False), (" off\n", False), (False, False), (0, False),
        ],
    )
    def test_accepted_words(self, value, expected):
        assert parse_bool(value, not expected) is expected

    @pytest.mark.parametrize("value", [None, "", "   ", "maybe", "2", "y", "enable"])
    @pytest.mark.parametrize("default", [True, False, None])
    def test_anything_else_gives_the_default(self, value, default):
        assert parse_bool(value, default) is default

    def test_env_bool_reads_the_environment(self):
        with patch.dict("os.environ", {"SOME_SWITCH": "off"}):
            assert env_bool("SOME_SWITCH", True) is False
        with patch.dict("os.environ", {}, clear=True):
            assert env_bool("SOME_SWITCH", True) is True

    def test_resolve_setting_skips_an_unrecognised_word(self):
        """A stored value that is not a yes/no word falls through to the environment, then the default."""
        with patch.dict("os.environ", {"SOME_SWITCH": "on"}):
            assert resolve_setting({"k": "maybe"}, "k", env_var="SOME_SWITCH", default=False, caster=_cast_bool) is True
        with patch.dict("os.environ", {}, clear=True):
            assert resolve_setting({"k": "maybe"}, "k", env_var="SOME_SWITCH", default=True, caster=_cast_bool) is True
            assert resolve_setting({"k": "off"}, "k", env_var="SOME_SWITCH", default=True, caster=_cast_bool) is False


class TestEnableDockerOff:
    @pytest.fixture
    def test_db(self, tmp_path: Path) -> Path:
        db = tmp_path / "yes_no_test.db"
        run_migrations(db)
        return db

    @pytest.mark.asyncio
    async def test_env_off_keeps_the_local_host_off_when_the_web_ui_has_it_on(self, test_db: Path):
        pool = DockerCollectorPool(assembler=KeyedMultilineAssembler(asyncio.Queue()), db_path=test_db)

        async def _idle(*args, **kwargs):
            await asyncio.Event().wait()

        with patch.dict("os.environ", {"ENABLE_DOCKER": "off"}), \
                patch("app.collectors.docker_pool.get_cached_setting", return_value=True), \
                patch.object(DockerTailer, "run", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = _idle
            await pool.start()
            assert pool.get_tailer(1).enable_docker is False
            await pool.stop()

    def test_env_off_gives_no_local_docker_url(self):
        with patch.dict("os.environ", {"ENABLE_DOCKER": "off", "DOCKER_HOST": "unix:///var/run/docker.sock"}), \
                patch("app.core.config.get_cached_setting", return_value=True):
            assert _parse_docker_host() == ("", None)

    def test_first_run_seeds_the_local_host_off_for_env_off(self, tmp_path: Path):
        db = tmp_path / "seed.db"
        with patch.dict("os.environ", {"ENABLE_DOCKER": "off"}):
            run_migrations(db)
        from app.core.migrations import get_connection
        with get_connection(db) as conn:
            row = conn.execute("SELECT enabled FROM docker_endpoints WHERE id = 1").fetchone()
        assert row[0] == 0


class TestAdditionalDockerHostFlags:
    def test_string_flags_are_read_as_yes_no_words(self):
        hosts = [
            {"name": "a", "url": "tcp://10.0.0.1:2375", "enabled": "off", "tls_enabled": "false"},
            {"name": "b", "url": "tcp://10.0.0.2:2376", "enabled": True, "tls_enabled": "yes"},
        ]
        with patch.dict("os.environ", {"DOCKER_ADDITIONAL_HOSTS": json.dumps(hosts)}):
            parsed = get_docker_additional_hosts()
        assert [(h["enabled"], h["tls_enabled"]) for h in parsed] == [(0, 0), (1, 1)]


class TestStoredSwitches:
    def test_ai_follows_the_stored_switch(self):
        assert is_ai_enabled({"ai_enabled": "off"}, "gemini", "key") is False
        assert is_ai_enabled({"ai_enabled": "1"}, "gemini", "") is True

    def test_ai_never_saved_is_on_with_a_key_or_an_openai_compatible_server(self):
        assert is_ai_enabled({}, "gemini", "key") is True
        assert is_ai_enabled({}, "gemini", "") is False
        assert is_ai_enabled({}, "openai_compatible", "") is True

    def test_ai_requests_agree_with_the_settings_page_for_a_keyless_local_server(self):
        resolved = resolve_ai_provider_settings({"ai_provider": "openai_compatible"})
        assert resolved["ai_enabled"] is True

    def test_daily_digest_is_off_until_saved(self):
        assert is_daily_digest_enabled({}) is False
        assert is_daily_digest_enabled({"daily_digest_enabled": "on"}) is True
        assert is_daily_digest_enabled({"daily_digest_enabled": "0"}) is False
