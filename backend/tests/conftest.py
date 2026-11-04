"""
Pytest configuration and global fixtures for LogShed backend test suite.
"""

from pathlib import Path

import pytest
from argon2 import PasswordHasher
import app.main as main_mod
from app.core import security
from app.core.config import get_cors_origins, invalidate_settings_cache
from app.api.deps import reset_all_db_connections
from app.api.ingest import clear_ingest_token_cache
import app.services.drop_filter as drop_filter_mod

# Building the FastAPI app costs ~0.12s (route schema generation) and ~330 tests build one, so
# share instances. Swapped in before test modules run `from app.main import create_app`.
# The app only varies by CORS origins, ENABLE_API_DOCS and the presence of the built frontend; lifespan and
# request handling keep no per-app state. Use `create_app.uncached()` for a fresh instance.
_STATIC_DIR = Path(main_mod.__file__).resolve().parent / "static"
_uncached_create_app = main_mod.create_app
_app_cache: dict = {}


def _cached_create_app():
    key = (tuple(get_cors_origins()), main_mod._api_docs_enabled(), _STATIC_DIR.exists(), (_STATIC_DIR / "assets").exists())
    if key not in _app_cache:
        _app_cache[key] = _uncached_create_app()
    return _app_cache[key]


_cached_create_app.uncached = _uncached_create_app
main_mod.create_app = _cached_create_app


@pytest.fixture(autouse=True)
def auto_reset_settings_cache(monkeypatch):
    """Ensure in-memory settings cache is invalidated between test cases and on env changes."""
    invalidate_settings_cache()
    clear_ingest_token_cache()
    reset_all_db_connections()
    orig_setenv = monkeypatch.setenv
    orig_delenv = monkeypatch.delenv

    def custom_setenv(*args, **kwargs):
        orig_setenv(*args, **kwargs)
        invalidate_settings_cache()

    def custom_delenv(*args, **kwargs):
        orig_delenv(*args, **kwargs)
        invalidate_settings_cache()

    monkeypatch.setenv = custom_setenv
    monkeypatch.delenv = custom_delenv
    yield
    invalidate_settings_cache()
    clear_ingest_token_cache()
    reset_all_db_connections()


@pytest.fixture(autouse=True, scope="session")
def fast_password_hasher():
    """Use minimal Argon2id cost parameters in tests; production cost adds ~125ms per hash.
    Verification reads parameters from the hash itself, so production-cost hashes still verify."""
    original = security._password_hasher
    security._password_hasher = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    yield
    security._password_hasher = original


@pytest.fixture(autouse=True)
def reset_drop_filter_counts():
    """Clear drops counted in memory by the process-wide drop filter, so a test that drops logs without a
    flush cannot add them to a later test's rule with the same id (the drop rules list adds pending counts)."""
    def clear():
        drop_filter = drop_filter_mod._drop_filter
        # Some tests swap in a fake filter
        if isinstance(drop_filter, drop_filter_mod.DropFilter):
            for rule_id in drop_filter.get_all_pending_counts():
                drop_filter.reset_rule_count(rule_id)

    clear()
    yield
    clear()


@pytest.fixture(autouse=True)
def reset_shared_app_overrides():
    """Keep dependency overrides set by one test from leaking into others through a shared app."""
    yield
    for app in _app_cache.values():
        app.dependency_overrides.clear()


@pytest.fixture
def free_syslog_port(monkeypatch) -> int:
    """Set SYSLOG_PORT to a port that is free for both TCP and UDP on 127.0.0.1, for tests that run the lifespan."""
    import socket

    for _ in range(20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp:
            tcp.bind(("127.0.0.1", 0))
            port = tcp.getsockname()[1]
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
                try:
                    udp.bind(("127.0.0.1", port))
                except OSError:
                    continue
        monkeypatch.setenv("SYSLOG_PORT", str(port))
        return port
    raise RuntimeError("no free port for both TCP and UDP")
