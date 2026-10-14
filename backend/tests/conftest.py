"""
Pytest configuration and global fixtures for LogShed backend test suite.
"""

import pytest
from argon2 import PasswordHasher
from app.core import security
from app.core.config import invalidate_settings_cache
from app.api.deps import reset_all_db_connections
from app.api.ingest import clear_ingest_token_cache


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
